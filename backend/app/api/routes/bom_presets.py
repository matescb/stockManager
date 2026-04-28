from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.responses import ok
from app.domain.projects.models import BomImportPreset

router = APIRouter()


class PresetIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    config: dict


class PresetPatch(BaseModel):
    name: str | None = None
    config: dict | None = None


def _serialize(p: BomImportPreset) -> dict:
    return {
        "id": str(p.id),
        "name": p.name,
        "config": json.loads(p.config_json) if p.config_json else {},
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


@router.get("")
def list_presets(db: DbSession, ws: CurrentWorkspace):
    rows = list(
        db.execute(
            select(BomImportPreset)
            .where(BomImportPreset.workspace_id == ws.id)
            .where(BomImportPreset.archived_at.is_(None))
            .order_by(BomImportPreset.name)
        ).scalars()
    )
    return ok([_serialize(p) for p in rows])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_preset(payload: PresetIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    p = BomImportPreset(
        workspace_id=ws.id,
        name=payload.name,
        config_json=json.dumps(payload.config),
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(p)
    db.commit()
    return ok(_serialize(p))


def _get(db, ws_id, pid) -> BomImportPreset:
    p = db.get(BomImportPreset, pid)
    if not p or p.workspace_id != ws_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="preset not found")
    return p


@router.get("/{preset_id}")
def get_preset(preset_id: UUID, db: DbSession, ws: CurrentWorkspace):
    return ok(_serialize(_get(db, ws.id, preset_id)))


@router.patch("/{preset_id}")
def patch_preset(preset_id: UUID, payload: PresetPatch, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    p = _get(db, ws.id, preset_id)
    if payload.name is not None:
        p.name = payload.name
    if payload.config is not None:
        p.config_json = json.dumps(payload.config)
    p.updated_by = user.id
    db.commit()
    return ok(_serialize(p))


@router.delete("/{preset_id}")
def delete_preset(preset_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get(db, ws.id, preset_id)
    db.delete(p)
    db.commit()
    return ok(None, "deleted")
