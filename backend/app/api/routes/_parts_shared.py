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

from collections.abc import Sequence
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select

from app.api._helpers import assert_in_workspace
from app.core.errors import ErrorCodes, raise_http
from app.domain._quantity import quantity_out
from app.domain.categories.service import CategoryIndex, category_name_paths
from app.domain.custom_fields.models import CustomField
from app.domain.parts import lifecycle_compliance
from app.domain.parts.models import Part, PartProviderLink
from app.domain.parts.provider_fields import (
    is_provider_namespaced_key,
    is_provider_reserved_custom_field_key,
)
from app.domain.parts.provider_links import serialize_link

# Re-export request schemas from the canonical domain location (CQ-006).
# Kept importable here for back-compat with split files (#118 step 2-4).
from app.domain.parts.schemas import BulkDeleteIn, PartIn, PartPatch  # noqa: F401
from app.domain.parts.services.spec_columns import specs_for_parts
from app.domain.parts.services.stored_prices import stored_prices_for_parts
from app.domain.parts.spec_schema import (
    all_canonical_keys,
    category_slug_for,
    is_catalog_key,
    missing_mandatory,
)
from app.domain.stock.service import bulk_current_quantities


def audit_fields_comment(fields: list[str] | set[str]) -> str:
    """The `fields=a,b,c` audit comment every PATCH-shaped route writes."""
    if not fields:
        return "fields=none"
    return "fields=" + ",".join(sorted(fields))


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
        # A retired row is not an image. `archived_at` became live on this
        # table with the spec reconcile (A3); every reader filters it.
        .where(CustomField.archived_at.is_(None))
    ).all()
    return {pid: val for pid, val in rows}


def missing_specs_for_parts(
    db, ws_id, parts: list, *, index: CategoryIndex | None = None
) -> dict:
    """`part_id -> mandatory canonical spec keys nobody supplied`.

    TWO statements for the whole page, never one per row — this runs on
    the busiest listing in the app (`serialize_part_rows`), where a
    per-row lookup is 200 round-trips to render one badge:

      1. the workspace's categories, to turn each part's `category_id`
         into the name path `spec_schema.category_slug_for` reads. Skipped
         entirely when no part on the page has a category, and skipped
         again when the caller already holds the workspace's tree and
         passes it as `index` (the refresh route resolves a category
         moments earlier and would otherwise load it twice).
      2. the parts' `custom_fields` rows, narrowed to
         `all_canonical_keys()`. A page of 200 parts has thousands of
         provider rows and only these can answer the question.

    "Supplied" means a row exists, whatever wrote it: a value the user
    typed is still the value, so `manual` and `override` rows count. An
    archived row does not — it is retired data.

    A part with no category gets the common schema, whose only mandatory
    key is `package`. That is the schema's own statement, and it is what
    makes an uncategorized part visibly unfinished rather than silently
    complete.
    """
    part_ids = [p.id for p in parts]
    if not part_ids:
        return {}
    slug_by_category = {
        category_id: category_slug_for(path)
        for category_id, path in category_name_paths(
            db, ws_id=ws_id, category_ids={p.category_id for p in parts}, index=index
        ).items()
    }
    present: dict = {pid: set() for pid in part_ids}
    rows = db.execute(
        select(CustomField.object_id, CustomField.key)
        .where(CustomField.workspace_id == ws_id)
        .where(CustomField.object_type == "part")
        .where(CustomField.object_id.in_(part_ids))
        .where(CustomField.key.in_(all_canonical_keys()))
        .where(CustomField.archived_at.is_(None))
    ).all()
    for object_id, key in rows:
        present.setdefault(object_id, set()).add(key)
    return {
        p.id: missing_mandatory(slug_by_category.get(p.category_id), present.get(p.id, ()))
        for p in parts
    }


#: The spec keys `package_spec` carries (edaflow SM-016). `package` is the
#: canonical key every provider's "Package / Case" lands in (ADR-0034);
#: `case` is read too so a hand-entered one is not lost.
PACKAGE_SPEC_KEYS: tuple[str, ...] = ("package", "case")


def package_specs_for_parts(db, ws_id, part_ids: Sequence) -> dict:
    """`part_id -> {"package": str|None, "case": str|None}` in ONE query.

    Stored values only: nothing here calls a provider. Every key is
    present on every part, null when the part has no row.
    """
    found = specs_for_parts(db, ws_id=ws_id, part_ids=part_ids, keys=PACKAGE_SPEC_KEYS)
    return {
        pid: {key: found[pid][key]["value"] for key in PACKAGE_SPEC_KEYS}
        for pid in part_ids
    }


def is_curated_spec_key(key: str) -> bool:
    """A key `curated_specs` carries (edaflow SM-018).

    What MCP `get_part` lists under `specs`, minus provider-namespaced keys
    (`digikey:…`, `mouser:…`) and catalog metadata (`Unit price (10+)`,
    stock, lead time, RoHS — `spec_schema.is_catalog_key`): those are
    distributor data, and prices already come structured in `prices`.
    """
    return not (
        is_provider_reserved_custom_field_key(key)
        or is_provider_namespaced_key(key)
        or is_catalog_key(key)
    )


def curated_specs_for_parts(db, ws_id, part_ids: Sequence) -> dict:
    """`part_id -> {key: value}` in ONE query; `{}` for a part with none.

    Stored rows only, no provider call. Keys are returned as stored: the
    canonical snake_case keys (`tolerance`, `dielectric`, ADR-0034) and,
    on parts `spec-normalize` has not reached, upstream display keys such
    as `Tolerance` too.
    """
    out: dict = {pid: {} for pid in part_ids}
    if not part_ids:
        return out
    rows = db.execute(
        select(CustomField.object_id, CustomField.key, CustomField.value)
        .where(
            CustomField.workspace_id == ws_id,
            CustomField.object_type == "part",
            CustomField.object_id.in_(part_ids),
            CustomField.archived_at.is_(None),
        )
        .order_by(CustomField.key)
    ).all()
    for row in rows:
        if row.object_id in out and is_curated_spec_key(row.key):
            out[row.object_id][row.key] = row.value
    return out


def serialize_part(
    p: Part,
    *,
    on_hand: Decimal | None = None,
    reserved: Decimal | None = None,
    available: Decimal | None = None,
    image_url: str | None = None,
    provider_links: list[dict] | None = None,
    missing_specs: list[str] | None = None,
    specs: dict[str, dict[str, str | None]] | None = None,
    lifecycle: dict | None = None,
    package_spec: dict[str, str | None] | None = None,
    prices: list[dict] | None = None,
    curated_specs: dict[str, str | None] | None = None,
    documents: dict[str, list] | None = None,
) -> dict:
    """Serialize a Part for API responses.

    `provider_links`, `missing_specs` and `specs` are emitted only when
    the caller loaded them, and the key is *absent* — not `[]` / `{}` —
    when it wasn't: an empty array reads as "this part has no links" /
    "this part is complete", which is a different fact from "this response
    didn't look". Lists load them in one batched query per page
    (`provider_links_for_parts`, `missing_specs_for_parts`,
    `spec_columns.specs_for_parts`); detail-shaped responses load the one
    part's rows (`provider_links_for`). Responses that echo a part without
    touching any of them — create-part, for one — still pass nothing and
    still omit the keys.

    `lifecycle` is one entry of `lifecycle_compliance.for_parts`
    (`{"lifecycle": …, "compliance": …}`) and emits both keys, in exactly
    the shape MCP `get_part` returns (SM-004). Absent when not loaded.

    `package_spec` is `{"package", "case"}` from `package_specs_for_parts`
    (edaflow SM-016) — its own key, because `specs` already means the requested
    `?spec_columns=`. Absent when not loaded; its values are null when the
    part has none.

    `prices` is the part's entry from `stored_prices_for_parts`
    (edaflow SM-017): the stored price ladders as numbers, `[]` when the
    part has none. Absent when not loaded.

    `curated_specs` is `{key: value}` from `curated_specs_for_parts`
    (edaflow SM-018), `{}` when the part has none. Not `specs` for the
    same reason as `package_spec`. Absent when not loaded.

    `documents` is `domain/parts/services/documents.list_part_documents`
    (edaflow SM-020) — the same object MCP `list_part_documents` returns —
    emitted as `documents` (stored PDFs) and `documents_pending` (recorded,
    not downloaded). Detail only: it can hash a file, so not per list row.
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
    if specs is not None:
        # The per-category spec columns this request asked for
        # (`?spec_columns=`), as `{key: {value, value_num}}`. Only the
        # requested keys are here, and every one of them is, with both
        # values null when the part has no row — so a blank cell is
        # distinguishable from a column that was never requested. Alembic
        # 0083 / ADR-0034.
        out["specs"] = specs
    if provider_links is not None:
        out["provider_links"] = provider_links
    if missing_specs is not None:
        # The canonical keys this part's category says it must have and
        # nobody supplied (ADR-0034). The boolean is derived rather than
        # stored so the two can never disagree.
        out["missing_specs"] = missing_specs
        out["spec_incomplete"] = bool(missing_specs)
    if lifecycle is not None:
        out["lifecycle"] = lifecycle["lifecycle"]
        out["compliance"] = lifecycle["compliance"]
    if package_spec is not None:
        out["package_spec"] = package_spec
    if prices is not None:
        out["prices"] = prices
    if curated_specs is not None:
        out["curated_specs"] = curated_specs
    if documents is not None:
        out["documents"] = documents["documents"]
        out["documents_pending"] = documents["pending"]
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


def serialize_part_rows(
    db,
    *,
    ws_id,
    parts: list,
    spec_keys: Sequence[str] = (),
    index: CategoryIndex | None = None,
    ws=None,
) -> list[dict]:
    """Serialize a page of parts for a LIST response.

    Every per-row extra — image URL, on-hand, reserved, provider links,
    spec-column values — is one batched query for the whole page. Keeping
    them together here is the point: a future column that needs another
    lookup has an obvious place to add a *batched* one, and the list route
    never grows a per-row query by accident.

    `spec_keys` is empty on every caller but the parts list with
    `?spec_columns=`, and an empty list costs no query and emits no
    `specs` key — so the response is byte-identical to what it was before
    per-category spec columns existed.

    `index` is this workspace's already-loaded category tree, for
    `missing_specs_for_parts` to read the name paths out of instead of
    loading it again. The parts list passes it because a category-filtered
    request has already loaded the same tree to expand the filter.
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
    missing_map = missing_specs_for_parts(db, ws_id, parts, index=index)
    specs_map = specs_for_parts(db, ws_id=ws_id, part_ids=part_ids, keys=spec_keys)
    life_map = (
        lifecycle_compliance.for_parts(db, ws, part_ids) if ws is not None else {}
    )
    package_map = package_specs_for_parts(db, ws_id, part_ids)
    price_map = stored_prices_for_parts(db, ws_id, part_ids)
    curated_map = curated_specs_for_parts(db, ws_id, part_ids)
    return [
        serialize_part(
            p,
            on_hand=on_hand_map.get(p.id, 0),
            reserved=reserved_map.get(p.id, 0),
            image_url=image_urls.get(p.id),
            provider_links=links_map.get(p.id, []),
            missing_specs=missing_map.get(p.id, []),
            specs=specs_map.get(p.id) if spec_keys else None,
            lifecycle=life_map.get(p.id),
            package_spec=package_map.get(p.id),
            prices=price_map.get(p.id, []),
            curated_specs=curated_map.get(p.id, {}),
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
