from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
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
    # Hint for the upsert. Defaults to "manual" — provider data is written
    # via app.api.routes.parts.refresh_from_provider, which sets source
    # directly. The frontend doesn't pass this; it's here so the provider
    # path can reuse this surface if it ever needs to.
    source: Literal["provider", "manual", "override"] = "manual"


def _serialize(r: CustomField) -> dict:
    return {
        "id": str(r.id),
        "key": r.key,
        "value": r.value,
        "source": r.source,
        "original_value": r.original_value,
    }


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
    return ok([_serialize(r) for r in rows])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_or_update(payload: CustomFieldIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    """Upsert. Provider/override transitions:

    * existing.source='provider' and the value changes →
      existing.source becomes 'override'; the upstream value is moved
      to existing.original_value so the user can later restore it.
    * existing.source='override' and the new value matches
      existing.original_value → revert to 'provider', clear
      original_value.
    * existing.source='manual' or no existing row → plain upsert at
      payload.source (default 'manual').
    """
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
    new_value = payload.value
    if existing:
        if existing.source == "provider" and new_value != existing.value:
            # User just edited a provider-supplied row. Promote it to an
            # override and remember what came from upstream.
            existing.original_value = existing.value
            existing.source = "override"
            existing.value = new_value
        elif existing.source == "override":
            if new_value == existing.original_value:
                # Restore-by-edit: typing the original back reverts to a
                # provider row.
                existing.value = new_value
                existing.original_value = None
                existing.source = "provider"
            else:
                existing.value = new_value
        else:
            # manual or provider-write-with-same-value
            existing.value = new_value
            if existing.source == "manual" and payload.source == "provider":
                # Edge case: a provider path is back-filling a row that
                # was previously stored as manual (e.g. legacy rows
                # before this migration). Trust the explicit source.
                existing.source = "provider"
        existing.updated_by = user.id
        db.commit()
        return ok(_serialize(existing))

    cf = CustomField(
        workspace_id=ws.id,
        object_type=payload.object_type,
        object_id=payload.object_id,
        key=payload.key,
        value=new_value,
        source=payload.source,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(cf)
    db.commit()
    return ok(_serialize(cf))


@router.delete("/{cf_id}")
def delete(cf_id: UUID, db: DbSession, ws: CurrentWorkspace):
    row = db.get(CustomField, cf_id)
    if row and row.workspace_id == ws.id:
        db.delete(row)
        db.commit()
    return ok(None, "deleted")


@router.delete("/{cf_id}/override")
def restore_override(cf_id: UUID, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    """Reverts an `override` row back to `provider`, restoring the saved
    `original_value` as the live value. 400 if the row isn't an override."""
    row = db.get(CustomField, cf_id)
    if not row or row.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="row not found")
    if row.source != "override":
        raise HTTPException(
            status_code=400,
            detail=f"row is not an override (source={row.source})",
        )
    row.value = row.original_value
    row.original_value = None
    row.source = "provider"
    row.updated_by = user.id
    db.commit()
    return ok(_serialize(row))
