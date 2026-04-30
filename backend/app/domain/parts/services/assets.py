"""Download provider-supplied assets (part images, datasheets) into our
own UPLOAD_DIR so we don't depend on Mouser/DigiKey CDNs at render time.

The helper is content-addressed (sha256 of body), idempotent, and
fail-tolerant: a network timeout or oversize body returns `None` and
the caller falls back to the original remote URL — i.e. the worst case
is the same as today's behaviour, never worse.
"""
from __future__ import annotations

import hashlib
import os
from urllib.parse import urlparse

import httpx

from app.core.config import settings


_TIMEOUT_SEC = 10.0
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB ceiling — datasheet PDFs are usually 1-3 MB

# Content-Type → file extension. Falls through to URL-suffix inference
# for anything that doesn't match.
_EXT_BY_MIME: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "application/pdf": "pdf",
}


def _ext_from_url(url: str) -> str | None:
    """Best-effort extension from the URL path (handles query strings)."""
    path = urlparse(url).path
    if not path or "." not in path.rsplit("/", 1)[-1]:
        return None
    ext = path.rsplit(".", 1)[-1].lower()
    # Sanity bound — anything > 5 chars is almost certainly not a real ext.
    return ext if 1 <= len(ext) <= 5 and ext.isalnum() else None


def _ext_from_response(resp: httpx.Response, url: str) -> str:
    ct = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if ct in _EXT_BY_MIME:
        return _EXT_BY_MIME[ct]
    by_url = _ext_from_url(url)
    if by_url:
        return by_url
    # Fallback — write something rather than refuse. Browsers infer from
    # the Content-Type response header anyway when re-served.
    return "bin"


def _http_get(url: str) -> httpx.Response:
    """Network seam — patched by tests."""
    with httpx.Client(timeout=_TIMEOUT_SEC, follow_redirects=True) as client:
        return client.get(url)


def fetch_provider_asset(url: str, workspace_id: str, kind: str) -> str | None:
    """Download `url`, store it under `{UPLOAD_DIR}/parts/{ws_id}/{sha}.{ext}`,
    return the public path `/api/parts/assets/{ws_id}/{sha}.{ext}`.

    `kind` is informational ("image" / "datasheet") and only used for
    log lines; the on-disk layout doesn't separate kinds (content-addressed
    files are unique per body hash anyway).

    Returns None on:
      - empty / non-http URL
      - HTTP error (4xx/5xx)
      - body > _MAX_BYTES
      - any network exception
    Caller should fall back to the original URL in those cases.
    """
    if not url or not url.lower().startswith(("http://", "https://")):
        return None
    try:
        resp = _http_get(url)
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    body = resp.content
    if not body or len(body) > _MAX_BYTES:
        return None

    sha = hashlib.sha256(body).hexdigest()
    ext = _ext_from_response(resp, url)
    filename = f"{sha}.{ext}"

    # On-disk: {UPLOAD_DIR}/parts/{ws_id}/{filename}
    target_dir = os.path.join(settings().UPLOAD_DIR, "parts", str(workspace_id))
    target_path = os.path.join(target_dir, filename)
    if not os.path.exists(target_path):
        os.makedirs(target_dir, exist_ok=True)
        # Write to a sibling tmp + rename so a crashed write doesn't leave
        # a half-file under the canonical path.
        tmp_path = target_path + ".tmp"
        with open(tmp_path, "wb") as f:
            f.write(body)
        os.replace(tmp_path, target_path)

    # Public URL — served by GET /api/parts/assets/{ws_id}/{filename}.
    return f"/api/parts/assets/{workspace_id}/{filename}"
