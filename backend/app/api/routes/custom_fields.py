from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.responses import ok
from app.domain.custom_fields.models import CustomField

router = APIRouter()


class CustomFieldIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_type: str
    object_id: UUID
    key: str = Field(min_length=1, max_length=256)
    value: str | None = Field(default=None, max_length=1024)


@router.get("/by-object/{object_type}/{object_id}")
def list_for(object_type: str, object_id: UUID, db: DbSession, ws: CurrentWorkspace):
    rows = list(
        db.execute(
            select(CustomField)
            .where(CustomField.workspace_id == ws.id)
            .where(CustomField.object_type == object_type)
            .where(CustomField.object_id == object_id)
            .order_by(CustomField.key)
        ).scalars()
    )
    return ok([{"id": str(r.id), "key": r.key, "value": r.value} for r in rows])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_or_update(payload: CustomFieldIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    existing = (
        db.execute(
            select(CustomField)
            .where(CustomField.workspace_id == ws.id)
            .where(CustomField.object_type == payload.object_type)
            .where(CustomField.object_id == payload.object_id)
            .where(CustomField.key == payload.key)
        )
        .scalars()
        .first()
    )
    if existing:
        existing.value = payload.value
        existing.updated_by = user.id
        db.commit()
        return ok({"id": str(existing.id), "key": existing.key, "value": existing.value})
    cf = CustomField(
        workspace_id=ws.id,
        object_type=payload.object_type,
        object_id=payload.object_id,
        key=payload.key,
        value=payload.value,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(cf)
    db.commit()
    return ok({"id": str(cf.id), "key": cf.key, "value": cf.value})


@router.delete("/{cf_id}")
def delete(cf_id: UUID, db: DbSession, ws: CurrentWorkspace):
    row = db.get(CustomField, cf_id)
    if row and row.workspace_id == ws.id:
        db.delete(row)
        db.commit()
    return ok(None, "deleted")
