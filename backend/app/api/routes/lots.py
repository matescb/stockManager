from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.api.routes._stock_integrity import raise_integrity_as_409
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.errors import ErrorCodes, raise_http
from app.core.pagination import decode_cursor, paginate
from app.core.responses import ok
from app.domain.audit.service import log as _audit_log
from app.domain.lots.models import Lot
from app.domain.lots.schemas import LotAdjustIn, LotPatch
from app.domain.stock.models import StockEntry
from app.domain.stock.schemas import AdjustStockIn, MoveStockIn
from app.domain.stock.service import (
    StockError,
    adjust_stock,
    current_quantity,
    history_for_lot,
    move_stock,
)

router = APIRouter()


def _serialize(l: Lot, quantity: int | None = None) -> dict:
    return {
        "id": str(l.id),
        "part_id": str(l.part_id),
        "name": l.name,
        "serial_number": l.serial_number,
        "parent_lot_id": str(l.parent_lot_id) if l.parent_lot_id else None,
        "description": l.description,
        "comments": l.comments,
        "expiration_date": l.expiration_date.isoformat() if l.expiration_date else None,
        "source_type": l.source_type,
        "purchase_quantity": l.purchase_quantity,
        "purchase_unit_cost": float(l.purchase_unit_cost) if l.purchase_unit_cost is not None else None,
        "purchase_currency": l.purchase_currency,
        "current_quantity": quantity,
        "created_at": l.created_at.isoformat(),
    }


@router.get("")
def list_lots(
    db: DbSession,
    ws: CurrentWorkspace,
    limit: int = Query(default=200, le=1000),
):
    lots = list(
        db.execute(
            select(Lot)
            .where(Lot.workspace_id == ws.id)
            .order_by(Lot.created_at.desc())
            .limit(limit)
        ).scalars()
    )
    out = []
    for l in lots:
        q = current_quantity(db, workspace_id=ws.id, part_id=l.part_id, lot_id=l.id)
        out.append(_serialize(l, quantity=q))
    return ok(out)


def _get(db, ws_id, lot_id) -> Lot:
    l = db.get(Lot, lot_id)
    if not l or l.workspace_id != ws_id:
        raise_http(status.HTTP_404_NOT_FOUND, code=ErrorCodes.LOT_NOT_FOUND, message="lot not found")
    return l


@router.get("/{lot_id}")
def get_lot(lot_id: UUID, db: DbSession, ws: CurrentWorkspace):
    l = _get(db, ws.id, lot_id)
    q = current_quantity(db, workspace_id=ws.id, part_id=l.part_id, lot_id=l.id)
    return ok(_serialize(l, quantity=q))


@router.patch("/{lot_id}")
def patch_lot(lot_id: UUID, payload: LotPatch, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    l = _get(db, ws.id, lot_id)
    data = payload.model_dump(exclude_unset=True)
    if "expiration_date" in data and data["expiration_date"]:
        from datetime import date
        try:
            data["expiration_date"] = date.fromisoformat(data["expiration_date"])
        except ValueError:
            raise_http(400, code=ErrorCodes.LOT_INVALID_EXPIRATION_DATE, message="invalid expiration_date")
    for k, v in data.items():
        setattr(l, k, v)
    l.updated_by = user.id
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="lot.updated",
        target_type="lot",
        target_ids=[l.id],
        comment="fields=" + ",".join(sorted(data)),
    )
    q = current_quantity(db, workspace_id=ws.id, part_id=l.part_id, lot_id=l.id)
    return ok(_serialize(l, quantity=q))


@router.post("/{lot_id}/move")
def move_lot(lot_id: UUID, payload: MoveStockIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    l = _get(db, ws.id, lot_id)
    payload = payload.model_copy(update={"part_id": l.part_id, "source_lot_id": l.id})
    try:
        out_e, in_e = move_stock(db, workspace_id=ws.id, user_id=user.id, payload=payload)
    except StockError as exc:
        # `get_db` rolls back on raise (BE2-010).
        raise_http(400, code=ErrorCodes.LOT_MOVE_STOCK_ERROR, message=str(exc))
    except DBAPIError as exc:
        raise_integrity_as_409(exc)
    return ok({"out": str(out_e.id), "in": str(in_e.id)})


@router.post("/{lot_id}/adjust-count")
def adjust_lot(lot_id: UUID, payload: LotAdjustIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    l = _get(db, ws.id, lot_id)
    aip = AdjustStockIn(
        part_id=l.part_id,
        storage_location_id=payload.storage_location_id,
        lot_id=l.id,
        actual_quantity=payload.actual_quantity,
        comments=payload.comments,
    )
    try:
        e = adjust_stock(db, workspace_id=ws.id, user_id=user.id, payload=aip)
    except StockError as exc:
        raise_http(400, code=ErrorCodes.LOT_ADJUST_STOCK_ERROR, message=str(exc))
    except DBAPIError as exc:
        raise_integrity_as_409(exc)
    return ok({"id": str(e.id) if e else None, "delta": e.quantity_delta if e else 0})


def _serialize_entry(e: StockEntry) -> dict:
    return {
        "id": str(e.id),
        "quantity_delta": e.quantity_delta,
        "storage_location_id": str(e.storage_location_id) if e.storage_location_id else None,
        "operation_type": e.operation_type,
        "comments": e.comments,
        "occurred_at": e.occurred_at.isoformat(),
    }


@router.get("/{lot_id}/history")
def lot_history(
    lot_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    limit: int = Query(default=200, le=1000),
    cursor: str | None = Query(default=None),
    paged: bool = Query(default=False),
):
    l = _get(db, ws.id, lot_id)

    if cursor is not None or paged:
        # Cursor-paginated path (opt-in).
        decoded_cursor = decode_cursor(cursor) if cursor is not None else None
        stmt = (
            select(StockEntry)
            .where(StockEntry.workspace_id == ws.id)
            .where(StockEntry.lot_id == l.id)
        )
        rows, next_cursor = paginate(
            db,
            stmt,
            sort_col=StockEntry.occurred_at,
            id_col=StockEntry.id,
            cursor=decoded_cursor,
            limit=limit,
            asc=False,
        )
        return ok({"items": [_serialize_entry(e) for e in rows], "next_cursor": next_cursor})

    # Legacy bare-list path — unchanged (FE uses ?limit=200).
    rows = history_for_lot(db, workspace_id=ws.id, lot_id=l.id, limit=limit)
    return ok([_serialize_entry(e) for e in rows])
