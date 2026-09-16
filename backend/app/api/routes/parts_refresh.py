"""Provider refresh and secondary-link teardown.

POST   /{part_id}/refresh-from-provider     — re-run MPN lookup, reconcile custom fields
DELETE /{part_id}/provider-links/{provider} — drop a secondary provider's link + fields

Split out of `parts_assets.py` (CQ-002 line-count budget), which is back
to serving content-addressed assets only. Mounted under the same
/api/parts prefix in main.py, so no URL changed.

A workspace has ONE primary provider and any number of secondaries, and
each reconciles strictly inside its own custom-field namespace. See
ADR-0031 and `domain/parts/provider_fields.py`.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import select

from app.api.routes._parts_shared import (
    get_part as _get_part,
)
from app.api.routes._parts_shared import (
    missing_specs_for_parts as _missing_specs_for_parts,
)
from app.api.routes._parts_shared import (
    serialize_part as _serialize,
)
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.errors import ErrorCodes, raise_http
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import ok
from app.core.secrets import decrypt
from app.core.time import utcnow
from app.domain.audit.service import log as _audit_log
from app.domain.custom_fields.models import CustomField
from app.domain.parts.part_type import sync_part_type_and_log
from app.domain.parts.provider_credentials import credentials_for
from app.domain.parts.provider_fields import (
    KNOWN_PROVIDER_NAMES,
    PROVIDER_ASSET_CUSTOM_FIELD_KINDS,
    provider_wrote_custom_field_row,
)
from app.domain.parts.provider_links import (
    delete_link,
    get_link,
    links_for_part,
    serialize_link,
    upsert_link,
)
from app.domain.parts.providers import make_provider
from app.domain.parts.providers.base import ProviderUpstreamError
from app.domain.parts.services.assets import fetch_provider_asset
from app.domain.parts.services.provider_cache import lookup_fresh
from app.domain.parts.services.spec_reconcile import (
    apply_provider_category,
    reconcile_provider_specs,
)
from app.domain.stock.service import reserved_quantity, total_for_part

router = APIRouter()


def _asset_fields(r: dict, ws) -> dict[str, str]:
    """The primary's non-spec rows: image, datasheet, source URL.

    Assets are downloaded locally with the same fallback bulk-import
    uses — a failed download keeps the upstream URL. A SECONDARY gets
    none of this on purpose (ADR-0031): the primary already owns the
    part's image and datasheet, so a second content-addressed copy would
    cost a request per refresh to produce a field nothing renders.
    """
    fields: dict[str, str] = {}
    for key, asset_kind in PROVIDER_ASSET_CUSTOM_FIELD_KINDS.items():
        if r.get(key):
            local = fetch_provider_asset(r[key], str(ws.id), asset_kind)
            fields[key] = local or r[key]
    if r.get("source_url"):
        fields["source_url"] = str(r["source_url"])
    return fields


def _secondary_fields(r: dict) -> dict[str, str]:
    """A secondary's non-spec rows, still bare here — `reconcile_provider_specs`
    applies the `"{provider}:"` prefix and the key-width guard."""
    return {
        key: str(r[key])
        for key in ("source_url", "datasheet_url", "category")
        if r.get(key)
    }


@router.post("/{part_id}/refresh-from-provider")
@limiter.limit("60/minute", key_func=workspace_key)
def refresh_from_provider(
    request: Request,
    part_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
    provider: str | None = Query(default=None, max_length=40),
):
    """Re-run an MPN lookup against this part's stored MPN.

    `?provider=` selects which configured provider to refresh from.
    Omitted — or naming the workspace's own `parts_provider` — runs the
    PRIMARY flow: it owns the part columns (manufacturer / mpn /
    footprint, and description unless locally edited), `parts.linked_*`,
    and the un-namespaced `source='provider'` custom fields.

    Any other known provider runs as a SECONDARY: it writes no part
    column at all, only a `part_provider_links` row and custom fields
    under its own `"{provider}:"` prefix. Both tiers reconcile strictly
    inside their own namespace, so refreshing one never disturbs the
    other's rows.
    """
    p = _get_part(db, ws.id, part_id)
    if not (p.mpn or "").strip():
        raise_http(
            400,
            code=ErrorCodes.PART_PROVIDER_MISSING_MPN,
            message="part has no MPN to look up",
        )

    primary_name = (ws.parts_provider or "").strip().lower() or None
    requested = (provider or "").strip().lower() or None
    is_primary = requested is None or requested == primary_name

    if is_primary:
        client = make_provider(
            ws.parts_provider,
            decrypt(ws.parts_provider_api_key),
            decrypt(ws.parts_provider_api_secret),
        )
        if client is None:
            raise_http(
                400,
                code=ErrorCodes.PART_PROVIDER_NOT_CONFIGURED,
                message="no parts provider configured (set one in Workspace settings)",
            )
    else:
        if requested not in KNOWN_PROVIDER_NAMES:
            raise_http(
                422,
                code=ErrorCodes.PART_PROVIDER_UNKNOWN,
                message=f"unknown parts provider '{requested}'",
                provider=requested,
            )
        creds = credentials_for(db, ws, requested)
        client = make_provider(requested, *creds) if creds is not None else None
        if client is None:
            raise_http(
                400,
                code=ErrorCodes.PART_PROVIDER_NOT_CONFIGURED,
                message=(
                    f"no credentials configured for '{requested}' "
                    "(set them in Workspace settings)"
                ),
                provider=requested,
            )

    # Use lookup_fresh (not lookup_with_cache) — the operator explicitly
    # triggered a refresh, so we always hit upstream.  The fresh result is
    # written back to the cache so subsequent lookup_with_cache calls see it.
    try:
        out = lookup_fresh(client, p.mpn.strip())
    except ProviderUpstreamError as exc:
        raise_http(
            exc.status_code,
            code=ErrorCodes.PROVIDER_UPSTREAM_ERROR,
            message=exc.message,
            provider=exc.provider,
        )
    if not out.get("found") or not out.get("result"):
        # No link row is created for a miss — a part the provider has
        # never heard of is not linked to it.
        return ok(
            {
                "found": False,
                "message": out.get("message") or "no match",
                "provider": client.name,
            }
        )

    r = out["result"]
    if is_primary:
        p.manufacturer = r.get("manufacturer") or p.manufacturer
        new_mpn = r.get("mpn") or p.mpn
        if new_mpn:
            p.mpn = new_mpn
        fp = r.get("footprint")
        if fp:
            # On every refresh we let the provider drive footprint — same
            # treatment as manufacturer/mpn (provider-owned for linked parts).
            p.footprint = fp
        if not p.description_locally_edited:
            new_desc = r.get("description")
            if new_desc:
                p.description = new_desc
        p.linked_provider = client.name
        p.linked_external_id = r.get("mpn") or p.linked_external_id
        p.last_refresh_at = utcnow()
        p.updated_by = user.id
        extra_fields = _asset_fields(r, ws)
        # A part created `local` that the primary now owns IS linked;
        # leaving the column behind is what put 160 prod parts in the
        # wrong bucket. `meta` / `sub_assembly` are left alone.
        #
        # AFTER `_asset_fields`, not before: the audit write flushes, and
        # flushing here would hold the row lock on this `parts` row across
        # that call's two remote asset downloads (image + datasheet, up to
        # 45s each).
        sync_part_type_and_log(
            db,
            ws=ws,
            user=user,
            part=p,
            request_id=getattr(request.state, "request_id", None),
        )
    else:
        # Secondary: the part's own columns belong to the primary. Not one
        # of them is touched here.
        extra_fields = _secondary_fields(r)

    # Category before specs — it selects the spec schema, and both tiers
    # may fill a category the part does not have yet. One the user chose
    # is never overruled.
    category = apply_provider_category(
        db,
        ws_id=ws.id,
        part=p,
        provider_name=client.name,
        provider_category=r.get("category"),
        description=r.get("description"),
        user_id=user.id,
    )
    report = reconcile_provider_specs(
        db,
        ws_id=ws.id,
        part=p,
        provider_name=client.name,
        raw_specs=[
            ((s.get("key") or ""), (s.get("value") or "")) for s in (r.get("specs") or [])
        ],
        category_slug=category.slug,
        is_primary=is_primary,
        user_id=user.id,
        description=r.get("description"),
        extra_fields=extra_fields,
        request_id=getattr(request.state, "request_id", None),
    )

    link = upsert_link(
        db,
        workspace_id=ws.id,
        part_id=p.id,
        user_id=user.id,
        provider=client.name,
        external_id=r.get("mpn"),
        source_url=str(r["source_url"]) if r.get("source_url") else None,
        last_refresh_at=p.last_refresh_at if is_primary else None,
    )

    return ok(
        {
            "found": True,
            "provider": client.name,
            # `summary.skipped` counts fields whose namespaced key wouldn't
            # fit the column — always 0 on the primary path, which writes
            # bare keys. `archived` counts junk rows retired from the part.
            "summary": report.summary(),
            # The category the provider's taxonomy named when this
            # workspace has nowhere to file the part. Null when the part
            # was filed, or when the taxonomy said nothing we recognise.
            "category_suggestion": category.suggestion,
            "link": serialize_link(link),
            "part": _serialize(
                p,
                on_hand=total_for_part(db, workspace_id=ws.id, part_id=p.id),
                reserved=reserved_quantity(db, workspace_id=ws.id, part_id=p.id),
                provider_links=[
                    serialize_link(row)
                    for row in links_for_part(db, workspace_id=ws.id, part_id=p.id)
                ],
                missing_specs=_missing_specs_for_parts(db, ws.id, [p]).get(p.id, []),
            ),
        }
    )


@router.delete("/{part_id}/provider-links/{provider}")
@limiter.limit("60/minute", key_func=workspace_key)
def delete_provider_link(
    request: Request,
    part_id: UUID,
    provider: str,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    """Unlink a SECONDARY provider from this part.

    Drops the link row, deletes the `source='provider'` fields this
    provider wrote, and demotes its `override` rows to plain `manual` —
    the user edited those, so they survive as their own.

    "Wrote" is `custom_fields.provider` plus its `"{provider}:"`
    namespace, not the namespace alone: since A3 a secondary also writes
    un-namespaced CANONICAL keys (`resistance`), and leaving those behind
    would make an unlinked provider's data permanently unattributable.
    A row another provider stamped, and an unstamped row outside this
    namespace, are both left exactly as they are.

    The primary is not unlinkable here: that is `PATCH /api/parts/{id}`
    with `unlink_provider=true`, which also releases the part columns.

    The guard reads `ws.parts_provider`, NOT `p.linked_provider`. Which
    tier a provider occupies is a workspace-level fact; `linked_provider`
    only records which provider last drove this part's columns, and it is
    sticky — it survives an admin switching the workspace primary. Keying
    the guard off it would permanently strand a link that the workspace's
    own configuration now says is a secondary, with no route able to
    remove it. Releasing the part columns stays PATCH's job either way.
    """
    p = _get_part(db, ws.id, part_id)
    name = provider.strip().lower()

    if name and name == (ws.parts_provider or "").strip().lower():
        raise_http(
            400,
            code=ErrorCodes.PART_PROVIDER_LINK_IS_PRIMARY,
            message=(
                f"'{name}' is this workspace's primary provider; "
                "PATCH the part with unlink_provider=true instead"
            ),
            provider=name,
        )

    row = get_link(db, workspace_id=ws.id, part_id=p.id, provider=name)
    if row is None:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCodes.PART_PROVIDER_LINK_NOT_FOUND,
            message="provider link not found",
        )
    delete_link(db, row)

    field_rows = [
        cf
        for cf in db.execute(
            select(CustomField)
            .where(CustomField.workspace_id == ws.id)
            .where(CustomField.object_type == "part")
            .where(CustomField.object_id == p.id)
            .where(CustomField.source.in_(["provider", "override"]))
        ).scalars()
        if provider_wrote_custom_field_row(name, cf)
    ]
    removed = 0
    for cf in field_rows:
        if cf.source == "provider":
            db.delete(cf)
            removed += 1
        else:
            cf.source = "manual"
            cf.original_value = None
            cf.updated_by = user.id

    _audit_log(
        db,
        ws=ws,
        user=user,
        action="part.provider_unlinked",
        target_type="part",
        target_ids=[p.id],
        comment=f"provider={name}",
        request_id=getattr(request.state, "request_id", None),
    )
    return ok(
        {
            "provider": name,
            "removed_fields": removed,
            "provider_links": [
                serialize_link(link)
                for link in links_for_part(db, workspace_id=ws.id, part_id=p.id)
            ],
        }
    )
