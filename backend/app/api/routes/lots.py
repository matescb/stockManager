from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.responses import ok
from app.domain.lots.models import Lot
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="lot not found")
    return l


@router.get("/{lot_id}")
def get_lot(lot_id: UUID, db: DbSession, ws: CurrentWorkspace):
    l = _get(db, ws.id, lot_id)
    q = current_quantity(db, workspace_id=ws.id, part_id=l.part_id, lot_id=l.id)
    return ok(_serialize(l, quantity=q))


class LotPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    comments: str | None = None
    expiration_date: str | None = None
    serial_number: str | None = None


@router.patch("/{lot_id}")
def patch_lot(lot_id: UUID, payload: LotPatch, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    l = _get(db, ws.id, lot_id)
    data = payload.model_dump(exclude_unset=True)
    if "expiration_date" in data and data["expiration_date"]:
        from datetime import date
        try:
            data["expiration_date"] = date.fromisoformat(data["expiration_date"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail="invalid expiration_date") from e
    for k, v in data.items():
        setattr(l, k, v)
    l.updated_by = user.id
    db.commit()
    q = current_quantity(db, workspace_id=ws.id, part_id=l.part_id, lot_id=l.id)
    return ok(_serialize(l, quantity=q))


@router.post("/{lot_id}/move")
def move_lot(lot_id: UUID, payload: MoveStockIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    l = _get(db, ws.id, lot_id)
    payload = payload.model_copy(update={"part_id": l.part_id, "source_lot_id": l.id})
    try:
        out_e, in_e = move_stock(db, workspace_id=ws.id, user_id=user.id, payload=payload)
        db.commit()
    except StockError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    return ok({"out": str(out_e.id), "in": str(in_e.id)})


class LotAdjustIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actual_quantity: int
    storage_location_id: UUID | None = None
    comments: str | None = None


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
        db.commit()
    except StockError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    return ok({"id": str(e.id) if e else None, "delta": e.quantity_delta if e else 0})


@router.get("/{lot_id}/history")
def lot_history(lot_id: UUID, db: DbSession, ws: CurrentWorkspace):
    l = _get(db, ws.id, lot_id)
    rows = history_for_lot(db, workspace_id=ws.id, lot_id=l.id)
    return ok(
        [
            {
                "id": str(e.id),
                "quantity_delta": e.quantity_delta,
                "storage_location_id": str(e.storage_location_id) if e.storage_location_id else None,
                "operation_type": e.operation_type,
                "comments": e.comments,
                "occurred_at": e.occurred_at.isoformat(),
            }
            for e in rows
        ]
    )
