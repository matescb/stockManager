"""Create a linked Part from a provider lookup result.

Three callers: bulk-import-from-scan (`api/routes/parts_scan.py`),
BOM provider import (`domain/projects/bom_import_provider.py`) and their
tests. All three create from the workspace's PRIMARY provider.

Since A3 the spec rows are not written here: `services/spec_reconcile.py`
owns that for both the create and the refresh path, so there is one
statement of what a provider payload means rather than two that drifted.
What stays here is what is specific to *creating* — the part columns, the
asset downloads, and the category the new part is filed under.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from app.core.time import utcnow
from app.domain.categories.service import CategoryIndex
from app.domain.parts.models import Part
from app.domain.parts.provider_fields import PROVIDER_ASSET_CUSTOM_FIELD_KINDS
from app.domain.parts.services.assets import fetch_provider_asset
from app.domain.parts.services.provider_field_values import (
    truncate_provider_field_value,
)
from app.domain.parts.services.spec_reconcile import (
    apply_provider_category,
    reconcile_provider_specs,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ProviderImportOutcome",
    "create_from_provider_lookup",
    "truncate_provider_field_value",
]


@dataclass(frozen=True)
class ProviderImportOutcome:
    """The new part, plus what the import could not decide for it.

    `category_suggestion` is the category path the provider's taxonomy
    named when this workspace has no category to file it under. Surfaced
    per row by the callers, because "we know what this is and you have
    nowhere to put it" is actionable and silence is not.

    The reconcile's own counts are NOT returned. They are already written
    to `audit_log` by `reconcile_provider_specs`, and no create-path
    caller ever read them — a bulk import reports rows created, not spec
    keys written. The refresh route, which does surface them, holds the
    report directly.
    """

    part: Part
    category_suggestion: str | None


def create_from_provider_lookup(
    db,
    *,
    workspace_id: UUID,
    user_id: UUID | None,
    provider_name: str,
    mpn: str,
    lookup_result: dict,
    default_storage_location_id: UUID | None = None,
    is_primary: bool = True,
    request_id: str | None = None,
    category_index: CategoryIndex | None = None,
) -> ProviderImportOutcome:
    """Create a linked Part from an existing provider lookup result.

    Caller owns transaction/savepoint boundaries. This helper writes only
    the Part and its provider-backed custom fields; stock movements remain
    with stock services.

    `is_primary` defaults to True because every caller creates from the
    workspace's own `parts_provider`. It is a parameter rather than an
    assumption so a future create-from-a-secondary path cannot silently
    write un-namespaced catalog keys into the primary's namespace — and
    it is why the asset download is gated on it below.

    `category_index` lets a caller in a loop (bulk-import-from-scan, up
    to 50 parts) pay for the workspace's category rows once.
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

    # Category first: it picks the spec schema, and the schema is what
    # tells a resistor's `Temperature Coefficient` (ppm/°C) apart from a
    # ceramic capacitor's (a dielectric name) under the same vendor key.
    category = apply_provider_category(
        db,
        ws_id=workspace_id,
        part=p,
        provider_name=provider_name,
        provider_category=r.get("category"),
        description=r.get("description"),
        user_id=user_id,
        index=category_index,
    )
    reconcile_provider_specs(
        db,
        ws_id=workspace_id,
        part=p,
        provider_name=provider_name,
        raw_specs=[
            ((s.get("key") or ""), (s.get("value") or "")) for s in (r.get("specs") or [])
        ],
        category_slug=category.slug,
        is_primary=is_primary,
        user_id=user_id,
        description=r.get("description"),
        # A SECONDARY downloads no assets (ADR-0031): the primary already
        # owns the part's image and datasheet, and a second
        # content-addressed copy would cost a request per import to
        # produce a field nothing renders. It would also land under a
        # `"{provider}:"` prefix, which is not where `PartInfo` looks.
        extra_fields=_asset_fields(r, workspace_id) if is_primary else _link_fields(r),
        request_id=request_id,
        category_assigned=category.assigned,
    )
    return ProviderImportOutcome(part=p, category_suggestion=category.suggestion)


def _link_fields(r: dict) -> dict[str, str]:
    """What a SECONDARY records instead of assets: the upstream URLs, as-is."""
    return {
        key: str(r[key])
        for key in ("source_url", "datasheet_url", "category")
        if r.get(key)
    }


def _asset_fields(r: dict, workspace_id: UUID) -> dict[str, str]:
    """`image_url` / `datasheet_url` / `source_url`, downloaded locally.

    Same fallback semantics the refresh path uses: a failed download
    keeps the upstream URL, because a link that works beats no row.
    """
    fields: dict[str, str] = {}
    for key, asset_kind in PROVIDER_ASSET_CUSTOM_FIELD_KINDS.items():
        if r.get(key):
            local = fetch_provider_asset(r[key], str(workspace_id), asset_kind)
            fields[key] = local or r[key]
    if r.get("source_url"):
        fields["source_url"] = str(r["source_url"])
    return fields
