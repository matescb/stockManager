"""Download provider-supplied assets (part images, datasheets) into our
own UPLOAD_DIR so we don't depend on Mouser/DigiKey CDNs at render time.

The helper is content-addressed (sha256 of body), idempotent, and
fail-tolerant: a network timeout or oversize body returns `None` and
the caller falls back to the original remote URL — i.e. the worst case
is the same as today's behaviour, never worse.

Hardening notes (SEC2-006):
- Host allow-list. We only follow URLs whose hostname is on the
  shipped-provider allow-list AND whose DNS resolves to a
  globally-routable IP. This blocks SSRF into RFC1918 / loopback /
  link-local / metadata-service ranges.
- No redirects. `httpx.Client(..., follow_redirects=False)`. A 30x
  upstream is a refusal — we don't want a future Mouser CDN swap to
  silently broaden the egress surface, and the allow-list check would
  be useless if we then chased a Location: header to anywhere.
- No SVG. SVG is XML and can carry `<script>` / `xlink:href` payloads
  that the browser will execute when it renders the file inline. We
  drop `image/svg+xml` from the MIME map entirely; an upstream that
  serves SVG ends up written with `.bin` (and our serve route forces
  `Content-Disposition: attachment` for non-image MIMEs anyway).
- Magic-byte validation (SEC2-012). After downloading the body we check
  its leading bytes against known file signatures. If the sniffed type
  doesn't match the Content-Type-derived extension the download is
  rejected and we return None. This prevents a compromised provider CDN
  from delivering a payload that masquerades as an innocuous image or PDF.
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)


_TIMEOUT_SEC = 10.0
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB ceiling — datasheet PDFs are usually 1-3 MB
_CHUNK_SIZE = 64 * 1024  # 64 KB — streaming read granularity for the size-cap guard


@dataclass
class _AssetResponse:
    """Result of `_http_get` after size-capped streaming.

    `body` is `None` when the download was aborted because either the
    `Content-Length` header or the running stream total exceeded
    `_MAX_BYTES`. Returning the headers + status separately lets the
    caller distinguish "oversize" from "non-200" / "empty" without
    holding any oversized bytes in memory.
    """

    status_code: int
    headers: dict[str, str]
    body: bytes | None


# Content-Type → file extension. Falls through to URL-suffix inference
# for anything that doesn't match. SVG is intentionally absent — see
# the module docstring.
_EXT_BY_MIME: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "application/pdf": "pdf",
}

# Magic-byte signatures for supported file types.
# Each value is the byte prefix that must appear at the start of a valid file.
# "webp" is special: it uses the RIFF container with "WEBP" at offset 8; the
# prefix check only covers the RIFF header — a second check for "WEBP" is done
# inside _sniff_ext.
_MAGIC: dict[str, bytes] = {
    "jpg": b"\xff\xd8\xff",
    "png": b"\x89PNG",
    "pdf": b"%PDF",
    "gif": b"GIF8",
    "webp": b"RIFF",
}


def _sniff_ext(header: bytes) -> str | None:
    """Return the file-type extension whose magic bytes match `header`, or
    None if no known signature matches.

    Only the first 16 bytes of the body are needed (all _MAGIC prefixes are
    ≤ 8 bytes); callers should pass `body[:16]` to keep memory usage low.
    """
    for ext, magic in _MAGIC.items():
        if header[:len(magic)] == magic:
            # RIFF container is used by both WebP and WAV. Confirm the
            # "WEBP" marker at bytes 8-12 so we don't accept random RIFF.
            if ext == "webp" and header[8:12] != b"WEBP":
                continue
            return ext
    return None


# Hostnames the helper is permitted to fetch from. Keep this narrow:
# every entry here is part of the SSRF surface. New providers must be
# added explicitly, never via wildcards.
_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        # Mouser
        "www.mouser.com",
        "mouser.com",
        "media.mouser.com",
        "eu.mouser.com",
        # DigiKey
        "www.digikey.com",
        "digikey.com",
        "media.digikey.com",
        "mediacdn.digikey.com",
    }
)


def _ext_from_url(url: str) -> str | None:
    """Best-effort extension from the URL path (handles query strings)."""
    path = urlparse(url).path
    if not path or "." not in path.rsplit("/", 1)[-1]:
        return None
    ext = path.rsplit(".", 1)[-1].lower()
    # Sanity bound — anything > 5 chars is almost certainly not a real ext.
    return ext if 1 <= len(ext) <= 5 and ext.isalnum() else None


def _ext_from_response(headers: dict[str, str], url: str) -> str:
    ct = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if ct == "image/svg+xml":
        # Explicit refusal — the file lands as `.bin` and the serve
        # route forces an `attachment` disposition for non-images.
        log.warning("provider asset rejected: SVG content-type from %s", url)
        return "bin"
    if ct in _EXT_BY_MIME:
        return _EXT_BY_MIME[ct]
    by_url = _ext_from_url(url)
    if by_url == "svg":
        log.warning("provider asset rejected: .svg URL suffix from %s", url)
        return "bin"
    if by_url:
        return by_url
    # Fallback — write something rather than refuse. Browsers infer from
    # the Content-Type response header anyway when re-served.
    return "bin"


def _host_is_allowed(host: str) -> bool:
    """True if `host` is on the explicit provider allow-list AND its
    A-record resolves to a globally routable IP."""
    if not host:
        return False
    if host.lower() not in _ALLOWED_HOSTS:
        return False
    try:
        ip_str = socket.gethostbyname(host)
    except OSError:
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    # `is_global` excludes private (RFC1918), loopback, link-local,
    # multicast, reserved, and the AWS metadata range (169.254/16).
    return ip.is_global


def _http_get(url: str) -> _AssetResponse:
    """Network seam — patched by tests. Streams the response body and
    aborts as soon as the running byte total exceeds `_MAX_BYTES`,
    so a hostile multi-GB body can never be fully buffered into memory
    before the size cap fires (security #285).

    `follow_redirects=False` is load-bearing: a 30x upstream is treated
    as a refusal (returns the redirect itself, which the caller rejects
    because `status_code != 200`). Auto-following would void the host
    allow-list since the Location: header could point anywhere.

    The returned `_AssetResponse.body` is `None` when the download was
    aborted because of the size cap (either Content-Length pre-check or
    mid-stream chunk-counter); the caller treats that the same as any
    other refusal and returns `None` to its caller.
    """
    with httpx.Client(timeout=_TIMEOUT_SEC, follow_redirects=False) as client:
        with client.stream("GET", url) as resp:
            headers = dict(resp.headers)
            status = resp.status_code

            # Belt-and-braces: many CDNs send Content-Length, so we can
            # short-circuit oversize responses without reading the body.
            # Treated as advisory — a missing or malformed value falls
            # through to the chunk-counting guard below.
            cl = headers.get("content-length")
            if cl is not None:
                try:
                    if int(cl) > _MAX_BYTES:
                        log.warning(
                            "provider asset rejected: Content-Length %s > %d (%s)",
                            cl,
                            _MAX_BYTES,
                            url,
                        )
                        return _AssetResponse(status, headers, None)
                except ValueError:
                    pass

            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_bytes(chunk_size=_CHUNK_SIZE):
                total += len(chunk)
                if total > _MAX_BYTES:
                    log.warning(
                        "provider asset rejected: streamed body exceeded %d bytes (%s)",
                        _MAX_BYTES,
                        url,
                    )
                    return _AssetResponse(status, headers, None)
                chunks.append(chunk)

            return _AssetResponse(status, headers, b"".join(chunks))


def fetch_provider_asset(url: str, workspace_id: str, kind: str) -> str | None:
    """Download `url`, store it under `{UPLOAD_DIR}/parts/{ws_id}/{sha}.{ext}`,
    return the public path `/api/parts/assets/{ws_id}/{sha}.{ext}`.

    `kind` is informational ("image" / "datasheet") and only used for
    log lines; the on-disk layout doesn't separate kinds (content-addressed
    files are unique per body hash anyway).

    Returns None on:
      - empty / non-http URL
      - host not on the provider allow-list
      - host resolves to a non-public IP (SSRF guard)
      - HTTP error (4xx/5xx) or 30x redirect
      - body > _MAX_BYTES
      - magic bytes don't match the Content-Type-declared extension
      - any network exception
    Caller should fall back to the original URL in those cases.
    """
    if not url or not url.lower().startswith(("http://", "https://")):
        return None
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not _host_is_allowed(host):
        log.warning("provider asset rejected: host not allow-listed (%s)", host)
        return None
    try:
        resp = _http_get(url)
    except Exception:
        return None
    # 30x is treated as a refusal — we don't follow redirects (see
    # `_http_get` docstring).
    if resp.status_code != 200:
        return None
    # `body is None` signals the streaming guard aborted because the
    # response exceeded `_MAX_BYTES` (see `_http_get`). The size cap is
    # already enforced upstream; the caller just maps it to the same
    # "refusal" path as any other failure.
    body = resp.body
    if not body:
        return None

    ext = _ext_from_response(resp.headers, url)

    # Magic-byte validation (SEC2-012). Skip check for opaque .bin fallback —
    # those already carry a forced-download Content-Disposition when served.
    if ext != "bin":
        sniffed = _sniff_ext(body[:16])
        if sniffed != ext:
            log.warning(
                "provider asset rejected: magic bytes (%s) do not match "
                "declared extension (%s) from %s",
                sniffed or "<unknown>",
                ext,
                url,
            )
            return None

    sha = hashlib.sha256(body).hexdigest()
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
