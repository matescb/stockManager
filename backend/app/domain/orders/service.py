"""Order receiving service. Receiving emits stock ledger rows tagged with
the order_id/order_entry_id, and creates a Lot per receipt with
source_type='purchase' + source_order_id."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.lots.models import Lot
from app.domain.orders.models import Order, OrderEntry
from app.domain.orders.schemas import ReceiveIn
from app.domain.parts.models import Part
from app.domain.stock.models import StockEntry
from app.domain.storage.models import StorageLocation


class OrderError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


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

    entries_by_id: dict[UUID, OrderEntry] = {
        e.id: e
        for e in db.query(OrderEntry)
        .filter(OrderEntry.workspace_id == workspace_id, OrderEntry.order_id == order.id)
        .all()
    }

    received_at = _now()
    created_lots: list[Lot] = []
    created_entries: list[StockEntry] = []

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

        storage = None
        if line.storage_location_id is not None:
            storage = db.get(StorageLocation, line.storage_location_id)
            if storage is None or storage.workspace_id != workspace_id:
                raise OrderError("storage location not in workspace")
            if storage.archived_at is not None:
                raise OrderError("storage location is archived")
            if storage.is_full:
                raise OrderError("storage location is marked full")

        currency = oe.currency or order.currency
        unit_price = oe.unit_price

        lot_name = line.lot_name or f"{order.name}#{oe.order_index + 1}"
        lot = Lot(
            workspace_id=workspace_id,
            part_id=part.id,
            name=lot_name,
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

    return {
        "order_id": str(order.id),
        "status": order.status,
        "lots": [str(l.id) for l in created_lots],
        "stock_entries": [str(e.id) for e in created_entries],
    }
