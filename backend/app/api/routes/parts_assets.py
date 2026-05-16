"""Provider asset serving and provider-refresh endpoint.

GET  /assets/{ws_id}/{filename}        — serve a content-addressed provider asset
POST /{part_id}/refresh-from-provider  — re-run MPN lookup and reconcile custom fields

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
from app.domain.custom_fields.models import CustomField
from app.domain.parts.provider_fields import PROVIDER_ASSET_CUSTOM_FIELD_KINDS
from app.domain.parts.providers import make_provider
from app.domain.parts.providers.base import ProviderUpstreamError
from app.domain.parts.services.assets import fetch_provider_asset
from app.domain.parts.services.provider_cache import lookup_fresh
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
        safe = "".join(c for c in name if c.isalnum() or c in "._-")[:80] or "datasheet"
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


@router.post("/{part_id}/refresh-from-provider")
@limiter.limit("60/minute", key_func=workspace_key)
def refresh_from_provider(
    request: Request,
    part_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    """Re-run the workspace's configured MPN lookup against this part's
    stored MPN. Reconciles `source='provider'` custom_field rows
    (insert / update / delete) and never touches `manual` / `override`.
    Updates manufacturer + mpn + footprint always; description only when
    it hasn't been locally edited."""
    p = _get_part(db, ws.id, part_id)
    if not (p.mpn or "").strip():
        raise_http(
            400,
            code=ErrorCodes.PART_PROVIDER_MISSING_MPN,
            message="part has no MPN to look up",
        )

    provider = make_provider(
        ws.parts_provider,
        decrypt(ws.parts_provider_api_key),
        decrypt(ws.parts_provider_api_secret),
    )
    if provider is None:
        raise_http(
            400,
            code=ErrorCodes.PART_PROVIDER_NOT_CONFIGURED,
            message="no parts provider configured (set one in Workspace settings)",
        )

    # Use lookup_fresh (not lookup_with_cache) — the operator explicitly
    # triggered a refresh, so we always hit upstream.  The fresh result is
    # written back to the cache so subsequent lookup_with_cache calls see it.
    try:
        out = lookup_fresh(provider, p.mpn.strip())
    except ProviderUpstreamError as exc:
        raise_http(
            exc.status_code,
            code=ErrorCodes.PROVIDER_UPSTREAM_ERROR,
            message=exc.message,
            provider=exc.provider,
        )
    if not out.get("found") or not out.get("result"):
        return ok(
            {
                "found": False,
                "message": out.get("message") or "no match",
                "provider": provider.name,
            }
        )

    r = out["result"]
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
    p.linked_provider = provider.name
    p.linked_external_id = r.get("mpn") or p.linked_external_id
    p.last_refresh_at = utcnow()
    p.updated_by = user.id

    # Reconcile spec rows. For each provider-supplied (key, value):
    #   • existing row, source='provider'  → update value
    #   • existing row, source='manual'    → leave alone (user owns it)
    #   • existing row, source='override'  → leave alone, but remember the
    #     new upstream value as the new `original_value` so a Restore
    #     reflects current upstream, not historical.
    #   • absent                           → insert with source='provider'
    # After processing, any source='provider' row whose key isn't in the
    # upstream payload (and isn't a reserved system key) is deleted.
    desired: dict[str, str] = {}
    for s in r.get("specs") or []:
        key = (s.get("key") or "").strip()
        value = (s.get("value") or "").strip()
        if key:
            desired[key] = value
    # Download provider assets locally — same fallback semantics as
    # bulk-import: failed downloads keep the upstream URL.
    for key, asset_kind in PROVIDER_ASSET_CUSTOM_FIELD_KINDS.items():
        if r.get(key):
            local = fetch_provider_asset(r[key], str(ws.id), asset_kind)
            desired[key] = local or r[key]
    if r.get("source_url"):
        desired["source_url"] = str(r["source_url"])

    existing_rows = list(
        db.execute(
            select(CustomField)
            .where(CustomField.workspace_id == ws.id)
            .where(CustomField.object_type == "part")
            .where(CustomField.object_id == p.id)
        ).scalars()
    )
    by_key = {row.key: row for row in existing_rows}

    added = updated = removed = 0
    for key, value in desired.items():
        row = by_key.get(key)
        if row is None:
            db.add(
                CustomField(
                    workspace_id=ws.id,
                    object_type="part",
                    object_id=p.id,
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
            # Refresh the saved baseline so the Restore button reverts to
            # the current upstream value — not what was sent the first
            # time the part was linked.
            if row.original_value != value:
                row.original_value = value
                row.updated_by = user.id

    upstream_keys = set(desired.keys())
    for row in existing_rows:
        if row.source == "provider" and row.key not in upstream_keys:
            db.delete(row)
            removed += 1

    return ok(
        {
            "found": True,
            "provider": provider.name,
            "summary": {
                "added": added,
                "updated": updated,
                "removed": removed,
            },
            "part": _serialize(
                p,
                on_hand=total_for_part(db, workspace_id=ws.id, part_id=p.id),
                reserved=reserved_quantity(db, workspace_id=ws.id, part_id=p.id),
            ),
        }
    )
