"""Stock ledger service. Append-only — current_stock is always SUM(quantity_delta)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterable
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.domain.lots.models import Lot
from app.domain.parts.models import Part
from app.domain.stock.models import StockEntry
from app.domain.stock.schemas import (
    AddStockIn,
    AdjustStockIn,
    MoveStockIn,
    RemoveStockIn,
)
from app.domain.storage.models import StorageLocation
from app.domain.workspaces.models import Workspace


class StockError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _belongs(obj, workspace_id: UUID) -> bool:
    return obj is not None and obj.workspace_id == workspace_id


def current_quantity(
    db: Session,
    *,
    workspace_id: UUID,
    part_id: UUID,
    storage_location_id: UUID | None = None,
    lot_id: UUID | None = None,
    status: str = "on_hand",
) -> int:
    q = (
        select(func.coalesce(func.sum(StockEntry.quantity_delta), 0))
        .where(StockEntry.workspace_id == workspace_id)
        .where(StockEntry.part_id == part_id)
        .where(StockEntry.status == status)
    )
    if storage_location_id is not None:
        q = q.where(StockEntry.storage_location_id == storage_location_id)
    if lot_id is not None:
        q = q.where(StockEntry.lot_id == lot_id)
    return int(db.execute(q).scalar_one() or 0)


def stock_summary_for_part(
    db: Session, *, workspace_id: UUID, part_id: UUID, status: str = "on_hand"
) -> list[dict]:
    """Per-(storage, lot) breakdown of current stock for a part."""
    rows = db.execute(
        select(
            StockEntry.storage_location_id,
            StockEntry.lot_id,
            func.coalesce(func.sum(StockEntry.quantity_delta), 0).label("qty"),
        )
        .where(StockEntry.workspace_id == workspace_id)
        .where(StockEntry.part_id == part_id)
        .where(StockEntry.status == status)
        .group_by(StockEntry.storage_location_id, StockEntry.lot_id)
    ).all()
    return [
        {"storage_location_id": r[0], "lot_id": r[1], "quantity": int(r[2])}
        for r in rows
        if int(r[2]) != 0
    ]


def total_for_part(db: Session, *, workspace_id: UUID, part_id: UUID, status: str = "on_hand") -> int:
    return current_quantity(db, workspace_id=workspace_id, part_id=part_id, status=status)


def reserved_quantity(db: Session, *, workspace_id: UUID, part_id: UUID) -> int:
    """Net quantity reserved (planned but not consumed) for a part. Reserve
    rows add positive deltas; release rows add negatives so an equivalent
    release brings the total back to zero."""
    q = (
        select(func.coalesce(func.sum(StockEntry.quantity_delta), 0))
        .where(StockEntry.workspace_id == workspace_id)
        .where(StockEntry.part_id == part_id)
        .where(StockEntry.status == "reserved")
    )
    return int(db.execute(q).scalar_one() or 0)


def available_quantity(db: Session, *, workspace_id: UUID, part_id: UUID) -> int:
    """On-hand stock minus what is reserved for planned builds."""
    return (
        current_quantity(db, workspace_id=workspace_id, part_id=part_id)
        - reserved_quantity(db, workspace_id=workspace_id, part_id=part_id)
    )


def stock_for_storage(
    db: Session, *, workspace_id: UUID, storage_location_id: UUID, status: str = "on_hand"
) -> list[dict]:
    rows = db.execute(
        select(
            StockEntry.part_id,
            StockEntry.lot_id,
            func.coalesce(func.sum(StockEntry.quantity_delta), 0).label("qty"),
        )
        .where(StockEntry.workspace_id == workspace_id)
        .where(StockEntry.storage_location_id == storage_location_id)
        .where(StockEntry.status == status)
        .group_by(StockEntry.part_id, StockEntry.lot_id)
    ).all()
    return [
        {"part_id": r[0], "lot_id": r[1], "quantity": int(r[2])}
        for r in rows
        if int(r[2]) != 0
    ]


def add_stock(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    payload: AddStockIn,
) -> StockEntry:
    part = db.get(Part, payload.part_id)
    if not _belongs(part, workspace_id):
        raise StockError("part not found")
    storage = None
    if payload.storage_location_id:
        storage = db.get(StorageLocation, payload.storage_location_id)
        if not _belongs(storage, workspace_id):
            raise StockError("storage location not found")
        if storage.archived_at is not None:
            raise StockError("storage location is archived")
        if storage.is_full:
            raise StockError("storage location is marked full")

    # mandatory default-storage check (spec §19.2)
    if (
        part.default_storage_mandatory
        and part.default_storage_location_id
        and storage
        and storage.id != part.default_storage_location_id
    ):
        raise StockError("part requires default storage location")

    # Serial-tracking enforcement: when the workspace has serial tracking on
    # AND the part is flagged serialized, every stock addition must produce
    # exactly one serialized lot (quantity=1, serial_number required).
    ws = db.get(Workspace, workspace_id)
    if ws is not None and ws.serial_tracking_enabled and part.serialized:
        if payload.quantity != 1:
            raise StockError("serialized parts must be added one at a time (quantity=1)")
        if not payload.lot or not (payload.lot.serial_number or "").strip():
            raise StockError("serialized parts require lot.serial_number")

    unit_price: Decimal | None = None
    currency: str | None = None
    if payload.price and payload.price.mode != "none":
        currency = payload.price.currency
        if payload.price.mode == "per_component" and payload.price.unit_price is not None:
            unit_price = payload.price.unit_price
        elif payload.price.mode == "entire_lot" and payload.price.total_price is not None:
            unit_price = payload.price.total_price / payload.quantity

    lot: Lot | None = None
    if payload.lot is not None or unit_price is not None:
        exp: date | None = None
        if payload.lot and payload.lot.expiration_date:
            try:
                exp = date.fromisoformat(payload.lot.expiration_date)
            except ValueError as e:
                raise StockError("invalid expiration_date") from e
        lot = Lot(
            workspace_id=workspace_id,
            part_id=part.id,
            name=(payload.lot.name if payload.lot else None),
            comments=(payload.lot.comments if payload.lot else None),
            expiration_date=exp,
            serial_number=(payload.lot.serial_number if payload.lot else None),
            source_type="manual",
            purchase_quantity=payload.quantity,
            purchase_unit_cost=unit_price,
            purchase_currency=currency,
            created_by=user_id,
            updated_by=user_id,
        )
        db.add(lot)
        db.flush()

    entry = StockEntry(
        workspace_id=workspace_id,
        part_id=part.id,
        lot_id=(lot.id if lot else None),
        storage_location_id=(storage.id if storage else None),
        quantity_delta=payload.quantity,
        status="on_hand",
        unit_price=unit_price,
        currency=currency,
        operation_type="add",
        comments=payload.comments,
        occurred_at=_now(),
        created_by=user_id,
    )
    db.add(entry)
    db.flush()
    return entry


def remove_stock(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    payload: RemoveStockIn,
) -> StockEntry:
    part = db.get(Part, payload.part_id)
    if not _belongs(part, workspace_id):
        raise StockError("part not found")
    available = current_quantity(
        db,
        workspace_id=workspace_id,
        part_id=part.id,
        storage_location_id=payload.storage_location_id,
        lot_id=payload.lot_id,
    )
    if payload.quantity > available:
        raise StockError(f"insufficient stock (have {available}, want {payload.quantity})")
    entry = StockEntry(
        workspace_id=workspace_id,
        part_id=part.id,
        lot_id=payload.lot_id,
        storage_location_id=payload.storage_location_id,
        quantity_delta=-payload.quantity,
        status="on_hand",
        operation_type="remove",
        comments=payload.comments,
        occurred_at=_now(),
        created_by=user_id,
    )
    db.add(entry)
    db.flush()
    return entry


def move_stock(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    payload: MoveStockIn,
) -> tuple[StockEntry, StockEntry]:
    part = db.get(Part, payload.part_id)
    if not _belongs(part, workspace_id):
        raise StockError("part not found")
    dest = db.get(StorageLocation, payload.destination_storage_location_id)
    if not _belongs(dest, workspace_id):
        raise StockError("destination not found")
    if dest.archived_at is not None:
        raise StockError("destination is archived")
    if dest.is_full:
        raise StockError("destination is full")

    available = current_quantity(
        db,
        workspace_id=workspace_id,
        part_id=part.id,
        storage_location_id=payload.source_storage_location_id,
        lot_id=payload.source_lot_id,
    )
    if payload.quantity > available:
        raise StockError(f"insufficient stock at source (have {available}, want {payload.quantity})")

    if dest.single_part_only:
        # any other part already in this location?
        any_other = db.execute(
            select(func.count())
            .select_from(StockEntry)
            .where(StockEntry.workspace_id == workspace_id)
            .where(StockEntry.storage_location_id == dest.id)
            .where(StockEntry.part_id != part.id)
        ).scalar_one()
        if any_other:
            raise StockError("destination is single-part-only and holds another part")

    # lot for the moved-in side
    dest_lot_id = payload.source_lot_id
    if payload.split_lot and payload.source_lot_id is not None:
        src_lot = db.get(Lot, payload.source_lot_id)
        if not _belongs(src_lot, workspace_id):
            raise StockError("source lot not found")
        new_lot = Lot(
            workspace_id=workspace_id,
            part_id=part.id,
            name=f"{src_lot.name or 'lot'}-split",
            parent_lot_id=src_lot.id,
            description=src_lot.description,
            comments=f"split from {src_lot.id}",
            expiration_date=src_lot.expiration_date,
            source_type="split",
            purchase_quantity=payload.quantity,
            purchase_unit_cost=src_lot.purchase_unit_cost,
            purchase_currency=src_lot.purchase_currency,
            created_by=user_id,
            updated_by=user_id,
        )
        db.add(new_lot)
        db.flush()
        dest_lot_id = new_lot.id

    out_entry = StockEntry(
        workspace_id=workspace_id,
        part_id=part.id,
        lot_id=payload.source_lot_id,
        storage_location_id=payload.source_storage_location_id,
        quantity_delta=-payload.quantity,
        status="on_hand",
        operation_type="move_out",
        comments=payload.comments,
        occurred_at=_now(),
        created_by=user_id,
    )
    db.add(out_entry)
    db.flush()

    in_entry = StockEntry(
        workspace_id=workspace_id,
        part_id=part.id,
        lot_id=dest_lot_id,
        storage_location_id=dest.id,
        quantity_delta=payload.quantity,
        status="on_hand",
        operation_type="move_in",
        related_entry_id=out_entry.id,
        comments=payload.comments,
        occurred_at=_now(),
        created_by=user_id,
    )
    db.add(in_entry)
    db.flush()

    out_entry.related_entry_id = in_entry.id
    db.flush()
    return out_entry, in_entry


def adjust_stock(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    payload: AdjustStockIn,
) -> StockEntry | None:
    part = db.get(Part, payload.part_id)
    if not _belongs(part, workspace_id):
        raise StockError("part not found")
    current = current_quantity(
        db,
        workspace_id=workspace_id,
        part_id=part.id,
        storage_location_id=payload.storage_location_id,
        lot_id=payload.lot_id,
    )
    delta = payload.actual_quantity - current
    if delta == 0:
        return None
    entry = StockEntry(
        workspace_id=workspace_id,
        part_id=part.id,
        lot_id=payload.lot_id,
        storage_location_id=payload.storage_location_id,
        quantity_delta=delta,
        status="on_hand",
        operation_type="adjust",
        comments=payload.comments,
        occurred_at=_now(),
        created_by=user_id,
    )
    db.add(entry)
    db.flush()
    return entry


def history_for_part(
    db: Session, *, workspace_id: UUID, part_id: UUID, limit: int = 200
) -> list[StockEntry]:
    return list(
        db.execute(
            select(StockEntry)
            .where(StockEntry.workspace_id == workspace_id)
            .where(StockEntry.part_id == part_id)
            .order_by(StockEntry.occurred_at.desc())
            .limit(limit)
        ).scalars()
    )


def history_for_lot(db: Session, *, workspace_id: UUID, lot_id: UUID) -> list[StockEntry]:
    return list(
        db.execute(
            select(StockEntry)
            .where(StockEntry.workspace_id == workspace_id)
            .where(StockEntry.lot_id == lot_id)
            .order_by(StockEntry.occurred_at.desc())
        ).scalars()
    )


def history_for_storage(db: Session, *, workspace_id: UUID, storage_location_id: UUID) -> list[StockEntry]:
    return list(
        db.execute(
            select(StockEntry)
            .where(StockEntry.workspace_id == workspace_id)
            .where(StockEntry.storage_location_id == storage_location_id)
            .order_by(StockEntry.occurred_at.desc())
        ).scalars()
    )


def history_global(db: Session, *, workspace_id: UUID, limit: int = 500) -> list[StockEntry]:
    return list(
        db.execute(
            select(StockEntry)
            .where(StockEntry.workspace_id == workspace_id)
            .order_by(StockEntry.occurred_at.desc())
            .limit(limit)
        ).scalars()
    )
