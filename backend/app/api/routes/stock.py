from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.responses import Envelope, ok
from app.domain.stock.schemas import (
    AddStockIn,
    AdjustStockIn,
    MoveStockIn,
    RemoveStockIn,
)
from app.domain.parts.services.bag_signature import compute_bag_signature
from app.domain.stock.service import (
    StockConflictError,
    StockError,
    add_stock,
    adjust_stock,
    history_global,
    move_stock,
    remove_stock,
)

router = APIRouter()


def _serialize_entry(e):
    return {
        "id": str(e.id),
        "part_id": str(e.part_id),
        "lot_id": str(e.lot_id) if e.lot_id else None,
        "storage_location_id": str(e.storage_location_id) if e.storage_location_id else None,
        "quantity_delta": e.quantity_delta,
        "status": e.status,
        "unit_price": float(e.unit_price) if e.unit_price is not None else None,
        "currency": e.currency,
        "operation_type": e.operation_type,
        "comments": e.comments,
        "occurred_at": e.occurred_at.isoformat(),
    }


# `get_db` commits on clean exit and rolls back on raise (BE2-010), so
# these handlers don't need explicit db.commit()/db.rollback() — the
# StockError → 400 conversion just propagates and the dep handles the
# rollback.


@router.post("/add")
def add(
    payload: AddStockIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser
) -> Envelope[dict]:
    # Server-side bag_signature verification (BE2-015).  When raw_bag_code is
    # supplied alongside bag_signature, recompute the digest and reject on
    # mismatch.  When raw_bag_code is absent the client-supplied signature is
    # accepted verbatim (back-compat for callers that only send bag_signature).
    if payload.bag_signature and payload.raw_bag_code is not None:
        expected = compute_bag_signature(payload.raw_bag_code)
        if expected != payload.bag_signature:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"message": "bag_signature does not match recomputed digest of raw_bag_code"},
            )
    try:
        e = add_stock(db, workspace_id=ws.id, user_id=user.id, payload=payload)
    except StockConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": str(exc),
                "constraint": exc.constraint,
                "storage_location_id": str(exc.storage_location_id),
            },
        )
    except StockError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return ok(_serialize_entry(e))


@router.post("/remove")
def remove(
    payload: RemoveStockIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser
) -> Envelope[dict]:
    try:
        e = remove_stock(db, workspace_id=ws.id, user_id=user.id, payload=payload)
    except StockError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return ok(_serialize_entry(e))


@router.post("/move")
def move(payload: MoveStockIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    try:
        out_e, in_e = move_stock(db, workspace_id=ws.id, user_id=user.id, payload=payload)
    except StockConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": str(exc),
                "constraint": exc.constraint,
                "storage_location_id": str(exc.storage_location_id),
            },
        )
    except StockError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return ok({"out": _serialize_entry(out_e), "in": _serialize_entry(in_e)})


@router.post("/adjust")
def adjust(payload: AdjustStockIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    try:
        e = adjust_stock(db, workspace_id=ws.id, user_id=user.id, payload=payload)
    except StockError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return ok(_serialize_entry(e) if e is not None else None, "no change" if e is None else "OK")


@router.get("/history")
def history(db: DbSession, ws: CurrentWorkspace, limit: int = Query(default=200, le=1000)):
    rows = history_global(db, workspace_id=ws.id, limit=limit)
    return ok([_serialize_entry(e) for e in rows])
