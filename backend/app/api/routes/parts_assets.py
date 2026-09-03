"""Provider asset serving.

GET /assets/{ws_id}/{filename} — serve a content-addressed provider asset

The provider-refresh and secondary-link routes that used to live here
moved to `parts_refresh.py` (CQ-002 line-count budget); both modules
mount under the same /api/parts prefix in main.py, so no URL changed.

No URL structure changes from the original monolithic parts.py.
"""
from __future__ import annotations

import os
from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.deps import CurrentWorkspace
from app.core.errors import ErrorCodes, raise_http

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
