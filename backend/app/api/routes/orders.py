from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import and_, or_, select

from app.api._helpers import assert_child_in_parent, require_resource_access
from app.api.routes._activity import _DEFAULT_LIMIT, _MAX_LIMIT, build_activity
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.responses import ok
from app.domain.orders.models import Order, OrderEntry
from app.domain.stock.models import StockEntry
from app.domain.orders.schemas import (
    OrderCreateIn,
    OrderEntryIn,
    OrderEntryPatch,
    OrderPatchIn,
    ReceiveIn,
)
from app.domain.orders.service import OrderError, receive

router = APIRouter()
logger = logging.getLogger(__name__)


def _serialize(o: Order, *, totals: tuple[int, int] | None = None) -> dict:
    ordered, received = totals or (0, 0)
    return {
        "id": str(o.id),
        "name": o.name,
        "order_type": o.order_type,
        "supplier": o.supplier,
        "status": o.status,
        "ordered_on": o.ordered_on.isoformat() if o.ordered_on else None,
        "expected_on": o.expected_on.isoformat() if o.expected_on else None,
        "received_on": o.received_on.isoformat() if o.received_on else None,
        "currency": o.currency,
        "comments": o.comments,
        "archived_at": o.archived_at.isoformat() if o.archived_at else None,
        "totals": {"ordered": ordered, "received": received},
        "created_at": o.created_at.isoformat(),
        "updated_at": o.updated_at.isoformat(),
    }


def _serialize_entry(e: OrderEntry) -> dict:
    return {
        "id": str(e.id),
        "order_id": str(e.order_id),
        "part_id": str(e.part_id) if e.part_id else None,
        "name": e.name,
        "quantity_ordered": e.quantity_ordered,
        "quantity_received": e.quantity_received,
        "unit_price": float(e.unit_price) if e.unit_price is not None else None,
        "currency": e.currency,
        "comments": e.comments,
        "order_index": e.order_index,
    }


def _get_order(db, ws_id, oid) -> Order:
    o = db.get(Order, oid)
    if not o or o.workspace_id != ws_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="order not found")
    return o


def _entries_for(db, ws_id, oid) -> list[OrderEntry]:
    return list(
        db.execute(
            select(OrderEntry)
            .where(OrderEntry.workspace_id == ws_id)
            .where(OrderEntry.order_id == oid)
            .order_by(OrderEntry.order_index)
        ).scalars()
    )


def _totals(entries: list[OrderEntry]) -> tuple[int, int]:
    return (
        sum(e.quantity_ordered for e in entries),
        sum(e.quantity_received for e in entries),
    )


@router.get("")
def list_orders(
    db: DbSession,
    ws: CurrentWorkspace,
    archived: bool = False,
    q: str | None = None,
    order_status: str | None = None,
    limit: int = Query(default=200, le=1000),
):
    stmt = select(Order).where(Order.workspace_id == ws.id)
    stmt = stmt.where(Order.archived_at.is_(None) if not archived else Order.archived_at.is_not(None))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Order.name.ilike(like), Order.supplier.ilike(like), Order.comments.ilike(like)))
    if order_status:
        stmt = stmt.where(Order.status == order_status)
    stmt = stmt.order_by(Order.updated_at.desc()).limit(limit)
    out = []
    for o in db.execute(stmt).scalars():
        entries = _entries_for(db, ws.id, o.id)
        out.append(_serialize(o, totals=_totals(entries)))
    return ok(out)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderCreateIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    o = Order(
        workspace_id=ws.id,
        name=payload.name,
        order_type=payload.order_type,
        supplier=payload.supplier,
        ordered_on=payload.ordered_on,
        expected_on=payload.expected_on,
        currency=payload.currency,
        comments=payload.comments,
        status="draft" if not payload.entries else "open",
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(o)
    db.flush()
    for idx, ein in enumerate(payload.entries):
        db.add(
            OrderEntry(
                workspace_id=ws.id,
                order_id=o.id,
                part_id=ein.part_id,
                name=ein.name,
                quantity_ordered=ein.quantity_ordered,
                unit_price=ein.unit_price,
                currency=ein.currency,
                comments=ein.comments,
                order_index=idx,
                created_by=user.id,
                updated_by=user.id,
            )
        )
    db.flush()
    entries = _entries_for(db, ws.id, o.id)
    return ok(_serialize(o, totals=_totals(entries)))


@router.get("/{order_id}")
def get_order(order_id: UUID, db: DbSession, ws: CurrentWorkspace):
    o = _get_order(db, ws.id, order_id)
    entries = _entries_for(db, ws.id, o.id)
    return ok({
        "order": _serialize(o, totals=_totals(entries)),
        "entries": [_serialize_entry(e) for e in entries],
    })


@router.patch("/{order_id}")
def patch_order(order_id: UUID, payload: OrderPatchIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    o = _get_order(db, ws.id, order_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(o, k, v)
    o.updated_by = user.id
    entries = _entries_for(db, ws.id, o.id)
    return ok(_serialize(o, totals=_totals(entries)))


# Archive/restore — `require_resource_access` enforces resource-existence
# BEFORE the role check (BE2-009). A non-admin probing a foreign
# workspace's order_id gets 404, not 403.
@router.post("/{order_id}/archive")
def archive_order(order_id: UUID, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    from sqlalchemy import func, select as sa_select
    from app.domain.attachments.models import Attachment
    from app.domain.custom_fields.models import CustomField as CF
    from app.domain.tags.models import TagLink

    o = require_resource_access(
        db, Order, order_id, ws=ws, user=user, role="admin", label="order"
    )
    o.archived_at = datetime.now(timezone.utc)

    def _count(Model, ws_id, obj_id):
        return db.execute(
            sa_select(func.count()).select_from(Model).where(
                Model.workspace_id == ws_id,
                Model.object_id == obj_id,
            )
        ).scalar_one()

    logger.info(
        "order archived",
        extra={
            "workspace_id": str(ws.id),
            "order_id": str(o.id),
            "polymorphic_attachments": _count(Attachment, ws.id, o.id),
            "polymorphic_custom_fields": _count(CF, ws.id, o.id),
            "polymorphic_tag_links": _count(TagLink, ws.id, o.id),
        },
    )
    return ok(None, "archived")


@router.post("/{order_id}/restore")
def restore_order(order_id: UUID, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    o = require_resource_access(
        db, Order, order_id, ws=ws, user=user, role="admin", label="order"
    )
    o.archived_at = None
    return ok(None, "restored")


@router.post("/{order_id}/entries", status_code=status.HTTP_201_CREATED)
def add_entry(order_id: UUID, payload: OrderEntryIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    o = _get_order(db, ws.id, order_id)
    next_idx = (
        db.execute(
            select(OrderEntry.order_index)
            .where(OrderEntry.workspace_id == ws.id)
            .where(OrderEntry.order_id == o.id)
            .order_by(OrderEntry.order_index.desc())
            .limit(1)
        ).scalar() or -1
    ) + 1
    e = OrderEntry(
        workspace_id=ws.id,
        order_id=o.id,
        part_id=payload.part_id,
        name=payload.name,
        quantity_ordered=payload.quantity_ordered,
        unit_price=payload.unit_price,
        currency=payload.currency,
        comments=payload.comments,
        order_index=next_idx,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(e)
    if o.status == "draft":
        o.status = "open"
    db.flush()
    return ok(_serialize_entry(e))


@router.patch("/{order_id}/entries/{entry_id}")
def patch_entry(order_id: UUID, entry_id: UUID, payload: OrderEntryPatch, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    o = _get_order(db, ws.id, order_id)
    e = assert_child_in_parent(db, OrderEntry, entry_id, o, parent_fk="order_id", label="entry")
    data = payload.model_dump(exclude_unset=True)
    if "quantity_ordered" in data and data["quantity_ordered"] is not None:
        if data["quantity_ordered"] < e.quantity_received:
            raise HTTPException(
                status_code=400,
                detail="quantity_ordered cannot be less than already-received quantity",
            )
    for k, v in data.items():
        setattr(e, k, v)
    e.updated_by = user.id
    return ok(_serialize_entry(e))


@router.delete("/{order_id}/entries/{entry_id}")
def del_entry(order_id: UUID, entry_id: UUID, db: DbSession, ws: CurrentWorkspace):
    o = _get_order(db, ws.id, order_id)
    e = assert_child_in_parent(db, OrderEntry, entry_id, o, parent_fk="order_id", label="entry")
    if e.quantity_received > 0:
        raise HTTPException(status_code=400, detail="cannot delete entry with received stock")
    db.delete(e)
    return ok(None, "deleted")


@router.post("/{order_id}/receive")
def receive_order(order_id: UUID, payload: ReceiveIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    o = _get_order(db, ws.id, order_id)
    try:
        result = receive(db, workspace_id=ws.id, user_id=user.id, order=o, payload=payload)
    except OrderError as exc:
        # Re-raise as a 4xx — `get_db` rolls back automatically when
        # the route raises (BE2-010), so we don't need an explicit
        # db.rollback() here.
        raise HTTPException(status_code=400, detail=str(exc))
    return ok(result)


@router.get("/{order_id}/activity")
def order_activity(
    request: Request,
    order_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    before_occurred_at: str | None = Query(default=None),
    before_id: UUID | None = Query(default=None),
):
    o = _get_order(db, ws.id, order_id)

    cursor_at: datetime | None = None
    if before_occurred_at is not None:
        try:
            cursor_at = datetime.fromisoformat(before_occurred_at)
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid before_occurred_at")

    stmt = (
        select(StockEntry)
        .where(StockEntry.workspace_id == ws.id)
        .where(StockEntry.order_id == o.id)
    )
    if cursor_at is not None and before_id is not None:
        stmt = stmt.where(
            or_(
                StockEntry.occurred_at < cursor_at,
                and_(
                    StockEntry.occurred_at == cursor_at,
                    StockEntry.id < before_id,
                ),
            )
        )
    stmt = stmt.order_by(StockEntry.occurred_at.desc(), StockEntry.id.desc()).limit(limit + 1)
    stock_rows = list(db.execute(stmt).scalars())

    if not hasattr(request.state, "user_cache"):
        request.state.user_cache = {}

    result = build_activity(
        db,
        stock_rows=stock_rows,
        created_at=o.created_at,
        updated_at=o.updated_at,
        created_by=o.created_by,
        updated_by=o.updated_by,
        created_kind="order_created",
        updated_kind="order_updated",
        limit=limit,
        include_synthetic=(cursor_at is None),
        user_cache=request.state.user_cache,
    )
    return ok(result)
