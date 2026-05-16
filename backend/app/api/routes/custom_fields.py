from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request, status
from sqlalchemy import select

from app.api._helpers import assert_in_workspace, assert_polymorphic_in_workspace
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.errors import ErrorCodes, raise_http
from app.core.responses import ok
from app.domain.audit.service import log as _audit_log
from app.domain.custom_fields.models import CustomField
from app.domain.custom_fields.schemas import CustomFieldIn
from app.domain.parts.provider_fields import is_provider_reserved_custom_field_key

router = APIRouter()


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
def create_or_update(
    request: Request,
    payload: CustomFieldIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    """Upsert. Provider/override transitions:

    * existing.source='provider' and the value changes →
      existing.source becomes 'override'; the upstream value is moved
      to existing.original_value so the user can later restore it.
    * existing.source='override' and the new value matches
      existing.original_value → revert to 'provider', clear
      original_value.
    * existing.source='manual' or no existing row → plain upsert as 'manual'.

    `source` is server-controlled — provider rows are only ever written
    through the refresh-from-provider path in
    domain/parts/services/provider.py. Without this guard a caller could
    POST {source: "provider"} and forge a provider-origin row.
    """
    if is_provider_reserved_custom_field_key(payload.key):
        raise_http(
            400,
            code=ErrorCodes.CUSTOM_FIELD_RESERVED_KEY,
            message=f"{payload.key!r} is reserved for provider-managed data",
            key=payload.key,
        )

    # Polymorphic FK validation: object_id must name a row in the current
    # workspace, and object_type must be a known resource. Without this
    # guard a caller in workspace B can store custom fields keyed to a
    # part_id owned by workspace A.
    assert_polymorphic_in_workspace(db, payload.object_type, payload.object_id, ws.id)

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
        original_source = existing.source
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
        existing.updated_by = user.id
        _audit_log(
            db,
            ws=ws,
            user=user,
            action="custom_field.updated",
            target_type="custom_field",
            target_ids=[existing.id],
            comment=f"object_type={payload.object_type} source={original_source}",
            request_id=getattr(request.state, "request_id", None),
        )
        return ok(_serialize(existing))

    cf = CustomField(
        workspace_id=ws.id,
        object_type=payload.object_type,
        object_id=payload.object_id,
        key=payload.key,
        value=new_value,
        source="manual",
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(cf)
    db.flush()
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="custom_field.created",
        target_type="custom_field",
        target_ids=[cf.id],
        comment=f"object_type={payload.object_type} source=manual",
        request_id=getattr(request.state, "request_id", None),
    )
    return ok(_serialize(cf))


@router.delete("/{cf_id}")
def delete(request: Request, cf_id: UUID, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    row = db.execute(
        select(CustomField)
        .where(CustomField.id == cf_id)
        .where(CustomField.workspace_id == ws.id)
    ).scalar_one_or_none()
    if row is not None:
        object_type = row.object_type
        db.delete(row)
        _audit_log(
            db,
            ws=ws,
            user=user,
            action="custom_field.deleted",
            target_type="custom_field",
            target_ids=[cf_id],
            comment=f"object_type={object_type}",
            request_id=getattr(request.state, "request_id", None),
        )
    return ok(None, "deleted")


@router.delete("/{cf_id}/override")
def restore_override(
    request: Request,
    cf_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    """Reverts an `override` row back to `provider`, restoring the saved
    `original_value` as the live value. 400 if the row isn't an override."""
    row = assert_in_workspace(db, CustomField, cf_id, ws.id, label="row")
    if row.source != "override":
        raise_http(
            400,
            code=ErrorCodes.CUSTOM_FIELD_NOT_OVERRIDE,
            message=f"row is not an override (source={row.source})",
        )
    row.value = row.original_value
    row.original_value = None
    row.source = "provider"
    row.updated_by = user.id
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="custom_field.override_restored",
        target_type="custom_field",
        target_ids=[row.id],
        comment=f"object_type={row.object_type}",
        request_id=getattr(request.state, "request_id", None),
    )
    return ok(_serialize(row))
