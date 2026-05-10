from __future__ import annotations

from uuid import UUID

from app.core.time import utcnow
from app.domain.custom_fields.models import CustomField
from app.domain.parts.models import Part
from app.domain.parts.services.assets import fetch_provider_asset


def create_from_provider_lookup(
    db,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    provider_name: str,
    mpn: str,
    lookup_result: dict,
    default_storage_location_id: UUID | None = None,
) -> Part:
    """Create a linked Part from an existing provider lookup result.

    Caller owns transaction/savepoint boundaries. This helper writes only the
    Part and provider-backed custom fields; stock movements remain with stock
    services.
    """
    r = lookup_result
    name = (r.get("description") or "").strip() or mpn
    if len(name) > 300:
        name = name[:300]

    p = Part(
        workspace_id=workspace_id,
        part_type="linked",
        name=name,
        manufacturer=(r.get("manufacturer") or None),
        mpn=(r.get("mpn") or mpn),
        description=(r.get("description") or None),
        footprint=(r.get("footprint") or None),
        attrition_percentage=0,
        attrition_min_quantity=0,
        default_storage_location_id=default_storage_location_id,
        default_storage_mandatory=False,
        serialized=False,
        linked_provider=provider_name,
        linked_external_id=(r.get("mpn") or mpn),
        last_refresh_at=utcnow(),
        description_locally_edited=False,
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(p)
    db.flush()

    for s in (r.get("specs") or []):
        key = (s.get("key") or "").strip()
        value = (s.get("value") or "").strip()
        if not key or not value:
            continue
        _add_provider_field(
            db,
            workspace_id=workspace_id,
            user_id=user_id,
            part_id=p.id,
            key=key,
            value=value,
        )

    if r.get("image_url"):
        local = fetch_provider_asset(r["image_url"], str(workspace_id), "image")
        _add_provider_field(
            db,
            workspace_id=workspace_id,
            user_id=user_id,
            part_id=p.id,
            key="image_url",
            value=local or r["image_url"],
        )
    if r.get("datasheet_url"):
        local = fetch_provider_asset(r["datasheet_url"], str(workspace_id), "datasheet")
        _add_provider_field(
            db,
            workspace_id=workspace_id,
            user_id=user_id,
            part_id=p.id,
            key="datasheet_url",
            value=local or r["datasheet_url"],
        )
    if r.get("source_url"):
        _add_provider_field(
            db,
            workspace_id=workspace_id,
            user_id=user_id,
            part_id=p.id,
            key="source_url",
            value=str(r["source_url"])[:1024],
        )

    return p


def _add_provider_field(
    db,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    part_id: UUID,
    key: str,
    value: str,
) -> None:
    db.add(
        CustomField(
            workspace_id=workspace_id,
            object_type="part",
            object_id=part_id,
            key=key,
            value=value[:1024],
            source="provider",
            created_by=user_id,
            updated_by=user_id,
        )
    )
