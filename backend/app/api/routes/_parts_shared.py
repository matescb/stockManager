"""Shared helpers + Pydantic schemas for the parts router family.

`backend/app/api/routes/parts.py` was 1209 lines (CQ-002) and growing —
the next-largest router is 4× smaller. The plan in #118 splits it into
focused files (parts CRUD, parts_assets, parts_provider, parts_bulk).
This module is step 1 of the split: it lifts the helpers and request
schemas that each split file would otherwise need to duplicate or
re-import from a moving target.

Nothing about behaviour changes here. Every function and schema below
is an exact lift from `parts.py`. Subsequent PRs (#118-step-2..4) move
endpoint groups out of `parts.py` and import from this module.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select

from app.api._helpers import assert_in_workspace
from app.core.errors import ErrorCodes, raise_http
from app.domain._quantity import quantity_out
from app.domain.custom_fields.models import CustomField
from app.domain.parts.models import Part, PartProviderLink
from app.domain.parts.provider_links import serialize_link

# Re-export request schemas from the canonical domain location (CQ-006).
# Kept importable here for back-compat with split files (#118 step 2-4).
from app.domain.parts.schemas import BulkDeleteIn, PartIn, PartPatch  # noqa: F401
from app.domain.stock.service import bulk_current_quantities


def audit_fields_comment(fields: list[str] | set[str]) -> str:
    """The `fields=a,b,c` audit comment every PATCH-shaped route writes."""
    if not fields:
        return "fields=none"
    return "fields=" + ",".join(sorted(fields))


def raise_mpn_conflict(existing: Part) -> None:
    """409 naming the part that already holds this MPN — the shape the
    create-part client reads `existing_id` / `existing_name` from."""
    raise_http(
        status.HTTP_409_CONFLICT,
        code=ErrorCodes.PART_MPN_CONFLICT,
        message=f"MPN already used by part \"{existing.name}\"",
        existing_id=str(existing.id),
        existing_name=existing.name,
    )


def image_urls_for_parts(db, ws_id, part_ids: list) -> dict:
    """Single-shot SELECT for the per-part image_url custom_field row,
    keyed by part_id. The /parts list endpoint uses this so we don't
    fire one query per row."""
    if not part_ids:
        return {}
    rows = db.execute(
        select(CustomField.object_id, CustomField.value)
        .where(CustomField.workspace_id == ws_id)
        .where(CustomField.object_type == "part")
        .where(CustomField.object_id.in_(part_ids))
        .where(CustomField.key == "image_url")
    ).all()
    return {pid: val for pid, val in rows}


def serialize_part(
    p: Part,
    *,
    on_hand: Decimal | None = None,
    reserved: Decimal | None = None,
    available: Decimal | None = None,
    image_url: str | None = None,
    provider_links: list[dict] | None = None,
) -> dict:
    """Serialize a Part for API responses.

    `provider_links` is emitted only when the caller loaded it, and the
    key is *absent* — not `[]` — when it wasn't: an empty array reads as
    "this part has no links", which is a different fact from "this
    response didn't look". Lists load them in one batched query per page
    (`provider_links_for_parts`); detail-shaped responses load the one
    part's rows (`provider_links_for`). Responses that echo a part
    without touching the link table — create-part, for one — still pass
    nothing and still omit the key.
    """
    if reserved is None:
        reserved = 0
    if available is None and on_hand is not None:
        available = on_hand - reserved
    out = {
        "id": str(p.id),
        "part_type": p.part_type,
        "name": p.name,
        "manufacturer": p.manufacturer,
        "mpn": p.mpn,
        "internal_part_number": p.internal_part_number,
        "description": p.description,
        "footprint": p.footprint,
        "notes_markdown": p.notes_markdown,
        "low_stock_report_quantity": quantity_out(p.low_stock_report_quantity),
        "attrition_percentage": float(p.attrition_percentage or 0),
        "attrition_min_quantity": (
            quantity_out(p.attrition_min_quantity)
            if p.attrition_min_quantity is not None
            else 0
        ),
        "category_id": str(p.category_id) if p.category_id else None,
        "default_storage_location_id": str(p.default_storage_location_id) if p.default_storage_location_id else None,
        "default_storage_mandatory": p.default_storage_mandatory,
        "serialized": p.serialized,
        "published": bool(p.published),
        "linked_provider": p.linked_provider,
        "linked_external_id": p.linked_external_id,
        "last_refresh_at": p.last_refresh_at.isoformat() if p.last_refresh_at else None,
        # "Last change" — maintained by the `WorkspaceOwned` mixin's
        # `onupdate`, so it needs no column of its own and no migration.
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        "description_locally_edited": bool(p.description_locally_edited),
        "archived_at": p.archived_at.isoformat() if p.archived_at else None,
        # `on_hand` / `reserved` / `available` arrive as exact Decimals
        # from the stock service; `quantity_out` keeps them JSON integers.
        "on_hand": quantity_out(on_hand),
        "reserved": quantity_out(reserved),
        "available": quantity_out(available) if available is not None else 0,
        # Sourced from the part's `image_url` custom_field — the post-
        # download local path or a fallback to the upstream URL when the
        # download failed. None when no image was ever attached.
        "image_url": image_url,
    }
    if provider_links is not None:
        out["provider_links"] = provider_links
    return out


def provider_links_for(db, ws_id, part_id) -> list[dict]:
    """Serialized `part_provider_links` rows for one part."""
    from app.domain.parts.provider_links import links_for_part

    return [
        serialize_link(row)
        for row in links_for_part(db, workspace_id=ws_id, part_id=part_id)
    ]


def provider_links_for_parts(db, ws_id, part_ids: list) -> dict:
    """Serialized `part_provider_links` rows for a whole page, keyed by
    part_id.

    ONE statement for the page — the per-part `provider_links_for` in a
    loop would be an N+1 on the busiest listing in the app, and a page of
    200 rows would fire 200 round-trips to render one column. Parts with
    no links map to `[]`, which is why the caller can hand the empty list
    straight to `serialize_part` and mean "looked, found none".

    Workspace-scoped like every other query here (CLAUDE.md invariant):
    the `part_id IN (...)` set is already this workspace's, and the
    explicit `workspace_id` predicate keeps that true even if a caller
    ever passes ids it did not scope itself.
    """
    if not part_ids:
        return {}
    out: dict = {pid: [] for pid in part_ids}
    rows = db.execute(
        select(PartProviderLink)
        .where(PartProviderLink.workspace_id == ws_id)
        .where(PartProviderLink.part_id.in_(part_ids))
        .where(PartProviderLink.archived_at.is_(None))
        .order_by(PartProviderLink.part_id, PartProviderLink.provider)
    ).scalars()
    for row in rows:
        out.setdefault(row.part_id, []).append(serialize_link(row))
    return out


def serialize_part_rows(db, *, ws_id, parts: list) -> list[dict]:
    """Serialize a page of parts for a LIST response.

    Every per-row extra — image URL, on-hand, reserved, provider links —
    is one batched query for the whole page. Keeping them together here
    is the point: a future column that needs another lookup has an
    obvious place to add a *batched* one, and the list route never grows
    a per-row query by accident.
    """
    part_ids = [p.id for p in parts]
    image_urls = image_urls_for_parts(db, ws_id, part_ids)
    on_hand_map = bulk_current_quantities(
        db, workspace_id=ws_id, part_ids=part_ids, status="on_hand"
    )
    reserved_map = bulk_current_quantities(
        db, workspace_id=ws_id, part_ids=part_ids, status="reserved"
    )
    links_map = provider_links_for_parts(db, ws_id, part_ids)
    return [
        serialize_part(
            p,
            on_hand=on_hand_map.get(p.id, 0),
            reserved=reserved_map.get(p.id, 0),
            image_url=image_urls.get(p.id),
            provider_links=links_map.get(p.id, []),
        )
        for p in parts
    ]


def get_part(db, ws_id, part_id, *, include_archived: bool = False) -> Part:
    """Fetch a workspace-owned Part. Default refuses archived rows so
    write paths (PATCH, substitute add/remove, BOM bind) can't bind
    against a part the workspace already retired (BE2-016). Read-only
    surfaces pass `include_archived=True` so the detail page and
    activity timeline still load for archived parts.
    """
    try:
        p = assert_in_workspace(db, Part, part_id, ws_id, label="part")
    except HTTPException:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCodes.PART_NOT_FOUND,
            message="part not found",
        )
    if not include_archived and p.archived_at is not None:
        # 404 (not 400) — the part is "not available" for binds. We
        # don't distinguish "doesn't exist" from "archived" because the
        # client treats both as "this id is dead".
        raise_http(status.HTTP_404_NOT_FOUND, code=ErrorCodes.PART_NOT_FOUND, message="part not found")
    return p
