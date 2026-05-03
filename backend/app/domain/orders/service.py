"""Order receiving service. Receiving emits stock ledger rows tagged with
the order_id/order_entry_id, and creates a Lot per receipt with
source_type='purchase' + source_order_id."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.time import utcnow
from app.domain.lots.models import Lot
from app.domain.orders.models import Order, OrderEntry

log = get_logger(__name__)
from app.domain.orders.schemas import ReceiveIn
from app.domain.parts.models import Part
from app.domain.stock.models import StockEntry
from app.domain.stock.service import enforce_storage_constraints, lock_parts_for_stock_write
from app.domain.storage.models import StorageLocation
from app.domain.workspaces.models import Workspace


class OrderError(Exception):
    pass


def _order_status(entries: list[OrderEntry]) -> str:
    if not entries:
        return "draft"
    total = sum(e.quantity_ordered for e in entries)
    received = sum(e.quantity_received for e in entries)
    if received == 0:
        return "open"
    if received < total:
        return "partial"
    return "received"


def receive(
    db: Session,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    order: Order,
    payload: ReceiveIn,
) -> dict:
    """Apply a partial or full receive against an order. All-or-nothing
    within the request — caller is responsible for the surrounding commit."""
    if order.status == "cancelled":
        raise OrderError("order is cancelled")

    # BE2-001 / #247: acquire the advisory stock-write lock BEFORE reading
    # OrderEntry rows. Reading first and locking second is a TOCTOU race —
    # a concurrent receive can slip through the `outstanding` guard while
    # both threads hold stale in-memory `quantity_received` values.
    #
    # Fix: run a lightweight preliminary query to collect the part_ids for
    # the lock call, acquire the lock, then re-query entries with
    # FOR UPDATE so the values we act on are authoritative and pinned
    # for the rest of this transaction.
    preliminary_entries = (
        db.query(OrderEntry.id, OrderEntry.part_id)
        .filter(OrderEntry.workspace_id == workspace_id, OrderEntry.order_id == order.id)
        .all()
    )
    lock_parts_for_stock_write(
        db,
        workspace_id=workspace_id,
        part_ids=[row.part_id for row in preliminary_entries if row.part_id is not None],
    )

    # Re-query with FOR UPDATE after the lock so quantity_received reflects
    # any in-flight writes that committed before we acquired the lock.
    entries_by_id: dict[UUID, OrderEntry] = {
        e.id: e
        for e in db.query(OrderEntry)
        .filter(OrderEntry.workspace_id == workspace_id, OrderEntry.order_id == order.id)
        .with_for_update()
        .all()
    }

    received_at = utcnow()
    created_lots: list[Lot] = []
    created_entries: list[StockEntry] = []

    ws = db.get(Workspace, workspace_id)
    serial_tracking_on = bool(ws and ws.serial_tracking_enabled)

    for line in payload.lines:
        oe = entries_by_id.get(line.order_entry_id)
        if oe is None:
            raise OrderError(f"order entry {line.order_entry_id} not in this order")
        if oe.part_id is None:
            raise OrderError("cannot receive an entry without a part — match it first")
        outstanding = oe.quantity_ordered - oe.quantity_received
        if line.quantity > outstanding:
            raise OrderError(
                f"line over-receives entry {oe.id} (outstanding {outstanding}, want {line.quantity})"
            )

        part = db.get(Part, oe.part_id)
        if part is None or part.workspace_id != workspace_id:
            raise OrderError("part not in workspace")

        if serial_tracking_on and part.serialized:
            if line.quantity != 1:
                raise OrderError(
                    f"serialized part {part.name} must be received one unit per line"
                )
            if not (line.serial_number or "").strip():
                raise OrderError(
                    f"serialized part {part.name} requires a serial_number on the receive line"
                )

        storage = None
        if line.storage_location_id is not None:
            storage = db.get(StorageLocation, line.storage_location_id)
            if storage is None or storage.workspace_id != workspace_id:
                raise OrderError("storage location not in workspace")
            if storage.archived_at is not None:
                raise OrderError("storage location is archived")
            if storage.is_full:
                raise OrderError("storage location is marked full")
            # BE-004 follow-up (#280): producer paths must enforce
            # single_part_only / existing_parts_only on the destination,
            # same as add_stock / move_stock. The per-part advisory lock
            # was acquired above via lock_parts_for_stock_write; the helper
            # additionally takes the per-storage lock for the cross-part
            # race. Raised StockConflictError surfaces as a 409 in the
            # route layer (mirror of routes/stock.py).
            enforce_storage_constraints(
                db, workspace_id=workspace_id, storage=storage, part_id=part.id
            )

        currency = oe.currency or order.currency
        unit_price = oe.unit_price

        lot_name = line.lot_name or f"{order.name}#{oe.order_index + 1}"
        lot = Lot(
            workspace_id=workspace_id,
            part_id=part.id,
            name=lot_name,
            serial_number=line.serial_number,
            source_type="purchase",
            source_order_id=order.id,
            purchase_quantity=line.quantity,
            purchase_unit_cost=unit_price,
            purchase_currency=currency,
            created_by=user_id,
            updated_by=user_id,
        )
        db.add(lot)
        db.flush()
        created_lots.append(lot)

        entry = StockEntry(
            workspace_id=workspace_id,
            part_id=part.id,
            lot_id=lot.id,
            storage_location_id=storage.id if storage else None,
            quantity_delta=line.quantity,
            status="on_hand",
            unit_price=unit_price,
            currency=currency,
            operation_type="receive",
            order_id=order.id,
            order_entry_id=oe.id,
            occurred_at=received_at,
            created_by=user_id,
        )
        db.add(entry)
        db.flush()
        created_entries.append(entry)

        oe.quantity_received += line.quantity
        oe.updated_by = user_id

    # Recompute order status
    order.status = _order_status(list(entries_by_id.values()))
    if payload.received_on is not None:
        order.received_on = payload.received_on
    elif order.status == "received" and order.received_on is None:
        order.received_on = received_at.date()
    order.updated_by = user_id

    log.info(
        "order received",
        extra={
            "workspace_id": str(workspace_id),
            "order_id": str(order.id),
            "status": order.status,
            "lots_created": len(created_lots),
            "entries_created": len(created_entries),
        },
    )
    return {
        "order_id": str(order.id),
        "status": order.status,
        "lots": [str(l.id) for l in created_lots],
        "stock_entries": [str(e.id) for e in created_entries],
    }
