from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import or_, select

from app.api._helpers import assert_in_workspace, require_resource_access
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.errors import ErrorCodes, raise_http
from app.core.pagination import decode_cursor, paginate
from app.core.responses import ok
from app.core.time import utcnow
from app.domain._quantity import quantity_out
from app.domain.audit.service import log as _audit_log
from app.domain.stock.models import StockEntry
from app.domain.stock.service import (
    StockConflictError,
    history_for_storage,
    stock_for_storage,
    validate_storage_constraint_flag_update,
)
from app.domain.storage.models import StorageLocation
from app.domain.storage.schemas import StorageIn, StoragePatch

router = APIRouter()


def _serialize(s: StorageLocation) -> dict:
    return {
        "id": str(s.id),
        "name": s.name,
        "description": s.description,
        "single_part_only": s.single_part_only,
        "existing_parts_only": s.existing_parts_only,
        "is_full": s.is_full,
        "archived_at": s.archived_at.isoformat() if s.archived_at else None,
    }


@router.get("")
def list_storage(
    db: DbSession,
    ws: CurrentWorkspace,
    archived: bool = Query(default=False),
    q: str | None = Query(default=None),
    limit: int = Query(default=200, le=1000),
):
    stmt = select(StorageLocation).where(StorageLocation.workspace_id == ws.id)
    stmt = stmt.where(
        StorageLocation.archived_at.is_(None)
        if not archived
        else StorageLocation.archived_at.is_not(None)
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                StorageLocation.name.ilike(like),
                StorageLocation.description.ilike(like),
            )
        )
    stmt = stmt.order_by(StorageLocation.name).limit(limit)
    return ok([_serialize(s) for s in db.execute(stmt).scalars()])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_storage(payload: StorageIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    s = StorageLocation(
        workspace_id=ws.id,
        name=payload.name,
        description=payload.description,
        single_part_only=payload.single_part_only,
        existing_parts_only=payload.existing_parts_only,
        is_full=payload.is_full,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(s)
    db.flush()
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="storage.created",
        target_type="storage_location",
        target_ids=[s.id],
    )
    return ok(_serialize(s))


def _get(db, ws_id, sid) -> StorageLocation:
    try:
        return assert_in_workspace(db, StorageLocation, sid, ws_id, label="storage")
    except HTTPException:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCodes.STORAGE_NOT_FOUND,
            message="storage not found",
        )


@router.get("/{storage_id}")
def get_storage(storage_id: UUID, db: DbSession, ws: CurrentWorkspace):
    return ok(_serialize(_get(db, ws.id, storage_id)))


@router.patch("/{storage_id}")
def patch_storage(
    storage_id: UUID,
    payload: StoragePatch,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    s = _get(db, ws.id, storage_id)
    data = payload.model_dump(exclude_unset=True)
    try:
        validate_storage_constraint_flag_update(
            db,
            workspace_id=ws.id,
            storage=s,
            requested_single_part_only=data.get("single_part_only"),
            requested_existing_parts_only=data.get("existing_parts_only"),
        )
    except StockConflictError as exc:
        raise_http(
            status.HTTP_409_CONFLICT,
            code=ErrorCodes.STORAGE_CONSTRAINT_VIOLATION,
            message=str(exc),
            constraint=exc.constraint,
            storage_location_id=str(exc.storage_location_id),
        )
    for k, v in data.items():
        setattr(s, k, v)
    s.updated_by = user.id
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="storage.updated",
        target_type="storage_location",
        target_ids=[s.id],
        comment="fields=" + ",".join(sorted(data)),
    )
    return ok(_serialize(s))


# Archive/restore — `require_resource_access` enforces resource-existence
# BEFORE the role check (BE2-009). A non-admin probing a foreign
# workspace's storage_id gets 404, not 403.
@router.post("/{storage_id}/archive")
def archive_storage(storage_id: UUID, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    s = require_resource_access(
        db, StorageLocation, storage_id, ws=ws, user=user, role="admin", label="storage",
    )
    # BE2-014 — archiving a storage that still holds stock would orphan
    # those rows in a location the UI hides. Refuse with a structured
    # 409 listing what's still inside so the operator can move it first.
    # Runs after the auth gate so an attacker probing for ws-membership
    # doesn't learn whether a foreign storage has stock.
    # AUD-063: reservations do not carry storage_location_id today, but if
    # that changes, archiving must not hide a location with reserved stock.
    on_hand = stock_for_storage(db, workspace_id=ws.id, storage_location_id=s.id)
    reserved = stock_for_storage(
        db, workspace_id=ws.id, storage_location_id=s.id, status="reserved"
    )
    blocking = [
        {
            "part_id": str(r["part_id"]) if r["part_id"] else None,
            "lot_id": str(r["lot_id"]) if r["lot_id"] else None,
            "quantity": int(r["quantity"]),
        }
        for r in [*on_hand, *reserved]
        if int(r["quantity"]) > 0
    ]
    if blocking:
        raise_http(
            status.HTTP_409_CONFLICT,
            code=ErrorCodes.STORAGE_HAS_STOCK,
            message="storage still holds on-hand stock; move or remove it first",
            blocking=blocking,
        )
    s.archived_at = utcnow()
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="storage.archived",
        target_type="storage_location",
        target_ids=[s.id],
    )
    return ok(None, "archived")


@router.post("/{storage_id}/restore")
def restore_storage(storage_id: UUID, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    s = require_resource_access(
        db, StorageLocation, storage_id, ws=ws, user=user, role="admin", label="storage",
    )
    s.archived_at = None
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="storage.restored",
        target_type="storage_location",
        target_ids=[s.id],
    )
    return ok(None, "restored")


@router.get("/{storage_id}/parts")
def storage_parts(storage_id: UUID, db: DbSession, ws: CurrentWorkspace):
    s = _get(db, ws.id, storage_id)
    rows = stock_for_storage(db, workspace_id=ws.id, storage_location_id=s.id)
    return ok(
        [
            {
                "part_id": str(r["part_id"]) if r["part_id"] else None,
                "lot_id": str(r["lot_id"]) if r["lot_id"] else None,
                "quantity": r["quantity"],
            }
            for r in rows
        ]
    )


def _serialize_storage_entry(e: StockEntry) -> dict:
    return {
        "id": str(e.id),
        "part_id": str(e.part_id) if e.part_id else None,
        "lot_id": str(e.lot_id) if e.lot_id else None,
        "quantity_delta": quantity_out(e.quantity_delta),
        "operation_type": e.operation_type,
        "comments": e.comments,
        "occurred_at": e.occurred_at.isoformat(),
    }


@router.get("/{storage_id}/history")
def storage_history(
    storage_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    limit: int = Query(default=200, le=1000),
    cursor: str | None = Query(default=None),
    paged: bool = Query(default=False),
):
    s = _get(db, ws.id, storage_id)

    if cursor is not None or paged:
        # Cursor-paginated path (opt-in).
        decoded_cursor = decode_cursor(cursor) if cursor is not None else None
        stmt = (
            select(StockEntry)
            .where(StockEntry.workspace_id == ws.id)
            .where(StockEntry.storage_location_id == s.id)
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
        return ok(
            {
                "items": [_serialize_storage_entry(e) for e in rows],
                "next_cursor": next_cursor,
            }
        )

    # Legacy bare-list path — unchanged (FE uses ?limit=200).
    rows = history_for_storage(db, workspace_id=ws.id, storage_location_id=s.id, limit=limit)
    return ok([_serialize_storage_entry(e) for e in rows])
