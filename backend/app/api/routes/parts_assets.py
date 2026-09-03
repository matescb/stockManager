"""Provider asset serving, provider-refresh, and secondary-link teardown.

GET    /assets/{ws_id}/{filename}          — serve a content-addressed provider asset
POST   /{part_id}/refresh-from-provider    — re-run MPN lookup and reconcile custom fields
DELETE /{part_id}/provider-links/{provider} — drop a secondary provider's link + fields

All endpoints share the /api/parts prefix (registered in main.py).
No URL structure changes from the original monolithic parts.py.
"""
from __future__ import annotations

import os
from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.routes._parts_shared import (
    get_part as _get_part,
)
from app.api.routes._parts_shared import (
    serialize_part as _serialize,
)
from app.core.config import settings
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.errors import ErrorCodes, raise_http
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import ok
from app.core.secrets import decrypt
from app.core.time import utcnow
from app.domain.audit.service import log as _audit_log
from app.domain.custom_fields.models import CustomField
from app.domain.parts.provider_credentials import credentials_for
from app.domain.parts.provider_fields import (
    CUSTOM_FIELD_KEY_MAX,
    KNOWN_PROVIDER_NAMES,
    PROVIDER_ASSET_CUSTOM_FIELD_KINDS,
    is_provider_namespaced_key,
    namespaced_custom_field_key,
    provider_owns_custom_field_key,
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
from app.domain.parts.services.provider_import import truncate_provider_field_value
from app.domain.stock.service import reserved_quantity, total_for_part

router = APIRouter()

# ---------------------------------------------------------------------------
# Provider assets (images + datasheets, downloaded at part-creation /
# refresh time and served from our own origin so the app keeps working
# when the upstream CDN rotates a URL or goes down).
#
# Files live at {UPLOAD_DIR}/parts/{ws_id}/{sha256}.{ext} — content-
# addressed, so the immutable cache header is safe and overwrites can't
# break in-flight requests.
# ---------------------------------------------------------------------------


# MIME-by-extension map for the serve route. Anything not in this set
# is treated as an opaque binary and forced to download as an attachment
# — which keeps a future provider-asset-MIME drift (e.g. an HTML page
# erroneously saved with a .bin extension) from rendering inline. SVG is
# intentionally absent; SEC2-006 / SEC2-011.
_ASSET_MIME_BY_EXT: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "pdf": "application/pdf",
}
_INLINE_EXTS: frozenset[str] = frozenset({"jpg", "jpeg", "png", "gif", "webp"})


@router.get("/assets/{ws_id}/{filename}")
def get_provider_asset(
    ws_id: UUID,
    filename: str,
    ws: CurrentWorkspace,
    name: str | None = Query(default=None, max_length=120),
):
    # Workspace-scoped: an operator can only fetch assets that live under
    # their workspace's folder. The `ws` dep already proves membership in
    # the request's current workspace; this matches them.
    if ws_id != ws.id:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCodes.PART_ASSET_NOT_FOUND,
            message="asset not found",
        )
    # Disallow path traversal — filename must be a flat content-addressed name.
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            code=ErrorCodes.PART_ASSET_INVALID_FILENAME,
            message="invalid filename",
        )

    abs_path = os.path.join(settings().UPLOAD_DIR, "parts", str(ws_id), filename)
    if not os.path.isfile(abs_path):
        raise_http(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCodes.PART_ASSET_NOT_FOUND,
            message="asset not found",
        )

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    served_mime = _ASSET_MIME_BY_EXT.get(ext, "application/octet-stream")
    # Image MIMEs may stay inline so <img> tags work; everything else
    # (PDFs, opaque binaries) is forced to download to neuter any
    # MIME-confusion path. Mirrors the attachments.py pattern.
    inline = ext in _INLINE_EXTS
    headers = {
        # Content-addressed → safe to cache for a year, never re-revalidate.
        "Cache-Control": "public, max-age=31536000, immutable",
        # Belt-and-braces against a future bug that lets a MIME differ
        # from the served extension.
        "X-Content-Type-Options": "nosniff",
    }
    if name:
        # `inline` keeps image preview working; the filename only comes
        # into play when the user does Save As. Restrict to a safe
        # subset and append the original extension so the saved file
        # opens in the right viewer.
        # ASCII-only: str.isalnum() alone admits Unicode letters, which
        # blow up Starlette's latin-1 header encoding with a 500.
        safe = "".join(
            c for c in name if c.isascii() and (c.isalnum() or c in "._-")
        )[:80] or "datasheet"
        ext_suffix = f".{ext}" if ext and not safe.lower().endswith(f".{ext.lower()}") else ""
        disposition_type = "inline" if inline else "attachment"
        headers["Content-Disposition"] = f'{disposition_type}; filename="{safe}{ext_suffix}"'
        return FileResponse(abs_path, media_type=served_mime, headers=headers)

    if inline:
        return FileResponse(abs_path, media_type=served_mime, headers=headers)
    # Non-image, no caller-supplied filename — still force attachment so
    # an `evil.bin` lands as a download rather than a rendered page.
    # Set the header explicitly rather than relying on Starlette's
    # `content_disposition_type` kwarg — that param was added in
    # 0.36+ and silently no-ops on older versions, leaving the response
    # without a Content-Disposition at all.
    headers["Content-Disposition"] = "attachment"
    return FileResponse(abs_path, media_type=served_mime, headers=headers)


def _reconcile_provider_fields(
    db,
    *,
    ws,
    part,
    user,
    desired: dict[str, str],
    owns_key,
) -> tuple[int, int, int]:
    """Reconcile one provider's `source='provider'` custom_field rows.

    For each provider-supplied (key, value):
      • existing row, source='provider'  → update value
      • existing row, source='manual'    → leave alone (user owns it)
      • existing row, source='override'  → leave alone, but remember the
        new upstream value as the new `original_value` so a Restore
        reflects current upstream, not historical.
      • absent                           → insert with source='provider'
    After processing, any source='provider' row whose key isn't in the
    upstream payload is deleted.

    `owns_key(key)` bounds all of that to this provider's namespace. It
    is the load-bearing argument: without it the delete pass at the end
    would treat every OTHER provider's rows as "absent from my payload"
    and drop them on each refresh. Returns (added, updated, removed).
    """
    scoped_rows = [
        row
        for row in db.execute(
            select(CustomField)
            .where(CustomField.workspace_id == ws.id)
            .where(CustomField.object_type == "part")
            .where(CustomField.object_id == part.id)
        ).scalars()
        if owns_key(row.key)
    ]
    by_key = {row.key: row for row in scoped_rows}

    added = updated = removed = 0
    for key, value in desired.items():
        row = by_key.get(key)
        if row is None:
            db.add(
                CustomField(
                    workspace_id=ws.id,
                    object_type="part",
                    object_id=part.id,
                    key=key,
                    value=value,
                    source="provider",
                    created_by=user.id,
                    updated_by=user.id,
                )
            )
            added += 1
        elif row.source == "provider":
            if row.value != value:
                row.value = value
                row.updated_by = user.id
                updated += 1
        elif row.source == "override":
            if row.original_value != value:
                row.original_value = value
                row.updated_by = user.id

    upstream_keys = set(desired.keys())
    for row in scoped_rows:
        if row.source == "provider" and row.key not in upstream_keys:
            db.delete(row)
            removed += 1
    return added, updated, removed


def _primary_desired_fields(r: dict, ws) -> dict[str, str]:
    """The primary provider's un-namespaced payload — unchanged behaviour.

    Assets are downloaded locally with the same fallback semantics as
    bulk-import: a failed download keeps the upstream URL.
    """
    desired: dict[str, str] = {}
    for s in r.get("specs") or []:
        key = (s.get("key") or "").strip()
        value = (s.get("value") or "").strip()
        # A spec whose name collides with a provider namespace would be
        # written by the primary and then be outside its own reconcile
        # scope — an orphan a secondary refresh would later delete. No
        # real payload has one; skip rather than create the hazard.
        if key and not is_provider_namespaced_key(key):
            desired[key] = value
    for key, asset_kind in PROVIDER_ASSET_CUSTOM_FIELD_KINDS.items():
        if r.get(key):
            local = fetch_provider_asset(r[key], str(ws.id), asset_kind)
            desired[key] = local or r[key]
    if r.get("source_url"):
        desired["source_url"] = str(r["source_url"])
    return desired


def _secondary_desired_fields(r: dict, provider_name: str) -> tuple[dict[str, str], int]:
    """A secondary provider's payload, every key under its own prefix.

    Returns `(desired, skipped)`. A field is SKIPPED when its namespaced
    key would exceed the `custom_fields.key` width: the prefix adds
    characters to an upstream name we don't control, and truncating the
    key instead would silently collide two different attributes onto one
    row. The count is reported in the response so a dropped field is
    visible rather than merely absent.

    Assets are NOT downloaded: the primary already owns the part's image
    and datasheet, so a second copy would burn storage and a request per
    refresh to produce a field nothing renders as an image. The upstream
    URL is stored as-is and the Sourcing tab links to it.
    """
    desired: dict[str, str] = {}
    skipped = 0

    def put(key: str, value: str) -> None:
        nonlocal skipped
        namespaced = namespaced_custom_field_key(provider_name, key)
        if len(namespaced) > CUSTOM_FIELD_KEY_MAX:
            skipped += 1
            return
        desired[namespaced] = truncate_provider_field_value(value)

    for s in r.get("specs") or []:
        key = (s.get("key") or "").strip()
        value = (s.get("value") or "").strip()
        if key:
            put(key, value)
    for key in ("source_url", "datasheet_url", "category"):
        if r.get(key):
            put(key, str(r[key]))
    return desired, skipped


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
        desired = _primary_desired_fields(r, ws)
        skipped = 0
    else:
        # Secondary: the part's own columns belong to the primary. Not one
        # of them is touched here.
        desired, skipped = _secondary_desired_fields(r, client.name)

    added, updated, removed = _reconcile_provider_fields(
        db,
        ws=ws,
        part=p,
        user=user,
        desired=desired,
        owns_key=lambda key: provider_owns_custom_field_key(
            client.name, key, is_primary=is_primary
        ),
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
            "summary": {
                "added": added,
                "updated": updated,
                "removed": removed,
                # Fields whose namespaced key wouldn't fit the column.
                # Always 0 on the primary path, which writes bare keys.
                "skipped": skipped,
            },
            "link": serialize_link(link),
            "part": _serialize(
                p,
                on_hand=total_for_part(db, workspace_id=ws.id, part_id=p.id),
                reserved=reserved_quantity(db, workspace_id=ws.id, part_id=p.id),
                provider_links=[
                    serialize_link(row)
                    for row in links_for_part(db, workspace_id=ws.id, part_id=p.id)
                ],
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

    Drops the link row, deletes that provider's namespaced
    `source='provider'` fields, and demotes its `override` rows to plain
    `manual` — the user edited those, so they survive as their own.
    Nothing outside the `"{provider}:"` namespace is touched, so the
    primary link and its fields are unaffected.

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
        if provider_owns_custom_field_key(name, cf.key, is_primary=False)
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
