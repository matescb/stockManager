from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select

from app.core.deps import CurrentUser, CurrentWorkspace, DbSession, require_role
from app.core.responses import ok
from app.domain.storage.models import StorageLocation
from app.domain.stock.service import (
    history_for_storage,
    stock_for_storage,
)

router = APIRouter()


class StorageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    single_part_only: bool = False
    existing_parts_only: bool = False
    is_full: bool = False


class StoragePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    single_part_only: bool | None = None
    existing_parts_only: bool | None = None
    is_full: bool | None = None


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
        StorageLocation.archived_at.is_(None) if not archived else StorageLocation.archived_at.is_not(None)
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(StorageLocation.name.ilike(like), StorageLocation.description.ilike(like)))
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
    db.commit()
    return ok(_serialize(s))


def _get(db, ws_id, sid) -> StorageLocation:
    s = db.get(StorageLocation, sid)
    if not s or s.workspace_id != ws_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="storage not found")
    return s


@router.get("/{storage_id}")
def get_storage(storage_id: UUID, db: DbSession, ws: CurrentWorkspace):
    return ok(_serialize(_get(db, ws.id, storage_id)))


@router.patch("/{storage_id}")
def patch_storage(storage_id: UUID, payload: StoragePatch, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    s = _get(db, ws.id, storage_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    s.updated_by = user.id
    db.commit()
    return ok(_serialize(s))


@router.post("/{storage_id}/archive", dependencies=[Depends(require_role("admin"))])
def archive_storage(storage_id: UUID, db: DbSession, ws: CurrentWorkspace):
    s = _get(db, ws.id, storage_id)
    s.archived_at = datetime.now(timezone.utc)
    db.commit()
    return ok(None, "archived")


@router.post("/{storage_id}/restore", dependencies=[Depends(require_role("admin"))])
def restore_storage(storage_id: UUID, db: DbSession, ws: CurrentWorkspace):
    s = _get(db, ws.id, storage_id)
    s.archived_at = None
    db.commit()
    return ok(None, "restored")


@router.get("/{storage_id}/parts")
def storage_parts(storage_id: UUID, db: DbSession, ws: CurrentWorkspace):
    s = _get(db, ws.id, storage_id)
    rows = stock_for_storage(db, workspace_id=ws.id, storage_location_id=s.id)
    return ok(
        [
            {
                "part_id": str(r["part_id"]),
                "lot_id": str(r["lot_id"]) if r["lot_id"] else None,
                "quantity": r["quantity"],
            }
            for r in rows
        ]
    )


@router.get("/{storage_id}/history")
def storage_history(storage_id: UUID, db: DbSession, ws: CurrentWorkspace):
    s = _get(db, ws.id, storage_id)
    rows = history_for_storage(db, workspace_id=ws.id, storage_location_id=s.id)
    return ok(
        [
            {
                "id": str(e.id),
                "part_id": str(e.part_id),
                "lot_id": str(e.lot_id) if e.lot_id else None,
                "quantity_delta": e.quantity_delta,
                "operation_type": e.operation_type,
                "comments": e.comments,
                "occurred_at": e.occurred_at.isoformat(),
            }
            for e in rows
        ]
    )
