"""Download remote assets (part images, datasheets) into our own UPLOAD_DIR
so we don't depend on a vendor CDN at render time.

The helper is content-addressed (sha256 of body), idempotent, and
fail-tolerant: a network timeout or oversize body returns `None` and
the caller falls back to the original remote URL — i.e. the worst case
is the same as today's behaviour, never worse.

Fetch policy (ADR-0033)
-----------------------
There are two policies. The **allow-listed** one is the default and the
original SEC2-006 behaviour: the hostname must be on `_ALLOWED_HOSTS`, the
narrow Mouser/DigiKey list.

The **unrestricted** one drops that list. It applies only when BOTH hold:
`kind` is in `_UNRESTRICTED_KINDS` (today: `"datasheet"`) AND the caller
passes `allow_any_host=True`. Datasheets live on 40+ manufacturer domains
(vishay, ti, we-online, analog, panasonic, yageo, murata, …) and the
allow-list meant essentially nothing localised.

The opt-in is what keeps the surface small: the ONLY caller that passes it
is the `datasheet-backfill` cron job (`services/datasheets.py`). Request-path
callers — provider import and provider refresh — keep the allow-list, so a
user-triggered request can never reach a host outside it, and can never be
made to block the single uvicorn worker (ADR-0012) on an arbitrary vendor.
ADR-0033 records the decision and the compensating controls; this docstring
is the short form.

Everything else is unchanged and applies to BOTH policies (with one
exception, the per-host throttle, called out at the end):

- **Pinned resolution.** `_resolve_pinned_ip` resolves the hostname ONCE
  via `getaddrinfo`, rejects the host if *any* returned address is not
  globally routable, and the request is then issued against that IP
  literal — `Host:` header and TLS SNI/cert-verification hostname both
  preserved. The old code resolved for the check and then let httpx
  resolve *again* when connecting, which left a DNS-rebinding window: a
  hostile authoritative server could answer public for the check and
  127.0.0.1 for the connect. With the allow-list gone for datasheets the
  IP check is the *primary* control, so that window had to close.
- **HTTPS only** on the unrestricted (datasheet) path. Plain HTTP is
  still tolerated for allow-listed hosts so nothing that works today
  regresses.
- **No redirects.** `httpx.Client(..., follow_redirects=False)`. A 30x
  upstream is a refusal. Auto-following would void every host/IP check
  because the `Location:` header could point anywhere — including back at
  a private address.
- **10 MB streaming cap** with a `Content-Length` pre-check and a
  mid-stream chunk-counter abort, so a hostile multi-GB body is never
  buffered.
- **No SVG.** SVG is XML and can carry `<script>` / `xlink:href` payloads
  that the browser will execute when it renders the file inline. We
  drop `image/svg+xml` from the MIME map entirely; an upstream that
  serves SVG ends up written with `.bin` (and our serve route forces
  `Content-Disposition: attachment` for non-image MIMEs anyway).
- **Magic-byte validation (SEC2-012).** After downloading the body we check
  its leading bytes against known file signatures. If the sniffed type
  doesn't match the Content-Type-derived extension the download is
  rejected. This prevents a compromised CDN from delivering a payload
  that masquerades as an innocuous image or PDF.
- **No credentials in the URL.** A `user:pass@host` URL is refused
  outright rather than silently forwarded to an arbitrary host.
- **Wall-clock budget.** httpx's `timeout` is per operation, so a host
  that dribbles a byte every 9s never trips it. `_MAX_WALL_CLOCK_SEC`
  bounds the whole fetch.

The per-host throttle is the one control that is NOT applied to both
policies. `_throttle_host` is a process-global `time.sleep`, so it belongs
only on the unrestricted path, which runs offline in the cron sidecar. The
allow-listed callers are request handlers — bulk-import-from-scan fetches
up to 50 images from one provider CDN inside a single 60s request, and
refresh-from-provider is a sync route on a single uvicorn worker — and a 2s
gap per fetch would take both out.

Log lines carry a redacted reference (`scheme://host/path`) — never the
query string, which is where a signed-URL token or credential would live.
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)


# httpx's `timeout` is PER OPERATION (connect, then each read), not a budget
# for the whole request. A host that dribbles one chunk every 9s resets the
# read timer forever and never trips it, so the size cap alone does not bound
# how long a single fetch can take. `_MAX_WALL_CLOCK_SEC` is the missing
# budget, enforced by the streaming loop.
_TIMEOUT_SEC = 10.0
_MAX_WALL_CLOCK_SEC = 30.0
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB ceiling — datasheet PDFs are usually 1-3 MB
_CHUNK_SIZE = 64 * 1024  # 64 KB — streaming read granularity for the size-cap guard

# Asset kinds exempt from the host allow-list (ADR-0033). Keep this set as
# small as the product actually needs: every kind added here loses the
# allow-list and keeps only the pinned-IP / HTTPS / redirect / size /
# magic-byte controls.
_UNRESTRICTED_KINDS: frozenset[str] = frozenset({"datasheet"})

_DEFAULT_PORT_BY_SCHEME: dict[str, int] = {"http": 80, "https": 443}


class _WallClockExceeded(Exception):
    """One fetch ran past `_MAX_WALL_CLOCK_SEC`.

    Distinct from `httpx.TimeoutException`: that fires when a single socket
    operation stalls, which a slow-dribble server never triggers.
    """


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


@dataclass(frozen=True)
class _FetchTarget:
    """A resolved, validated connection target.

    `request_url` carries the pinned IP literal in its authority, so httpx
    connects to exactly the address we validated — it never re-resolves the
    hostname. `host_header` and `sni_hostname` carry the original name so
    vhost routing and TLS certificate verification still see the real host.
    """

    request_url: str
    host_header: str
    sni_hostname: str
    ip: str
    redacted_ref: str


@dataclass(frozen=True)
class StoredAsset:
    """A remote asset that now lives under `UPLOAD_DIR`."""

    public_url: str
    storage_key: str
    filename: str
    sha256: str
    ext: str
    size_bytes: int
    mime_type: str | None


@dataclass(frozen=True)
class AssetFetchResult:
    """Outcome of one fetch attempt.

    Exactly one of `stored` / `failure_code` is set. `failure_code` is a
    short stable token (never free text, never a URL) so callers can
    persist it and operators can group on it.
    """

    stored: StoredAsset | None = None
    failure_code: str | None = None


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

# Reverse map used when registering a stored asset as an Attachment.
_MIME_BY_EXT: dict[str, str] = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "pdf": "application/pdf",
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


def mime_for_ext(ext: str) -> str | None:
    """Canonical MIME for a stored asset extension, or None for `.bin`."""
    return _MIME_BY_EXT.get(ext.lower())


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


# Hostnames the ALLOW-LISTED policy (images) is permitted to fetch from.
# Keep this narrow: every entry here is part of the SSRF surface. New
# providers must be added explicitly, never via wildcards. Datasheets no
# longer consult this list — ADR-0033.
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


def _redact(url: str) -> str:
    """`scheme://host/path` — userinfo, port and query string dropped.

    Log lines and audit comments must never carry a signed-URL token or
    embedded credential, and both live in the parts we drop here.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return "<unparseable>"
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if len(path) > 120:
        path = path[:120] + "…"
    return f"{parsed.scheme}://{host}{path}"


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
        log.warning("remote asset rejected: SVG content-type from %s", _redact(url))
        return "bin"
    if ct in _EXT_BY_MIME:
        return _EXT_BY_MIME[ct]
    by_url = _ext_from_url(url)
    if by_url == "svg":
        log.warning("remote asset rejected: .svg URL suffix from %s", _redact(url))
        return "bin"
    if by_url:
        return by_url
    # Fallback — write something rather than refuse. Browsers infer from
    # the Content-Type response header anyway when re-served.
    return "bin"


def _ip_is_publicly_routable(raw: str) -> bool:
    """True only for a globally routable unicast address.

    `is_global` already excludes private (RFC1918), loopback, link-local
    (169.254/16 — the cloud metadata range), multicast, reserved and
    unspecified addresses. The explicit re-checks below are belt-and-braces
    against interpreter-version drift in `is_global`, and the IPv4-mapped
    unwrap closes `::ffff:169.254.169.254`.
    """
    # A scoped IPv6 literal (fe80::1%eth0) — strip the zone before parsing.
    candidate = raw.split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return False
    return bool(ip.is_global)


def _resolve_pinned_ip(host: str, port: int) -> str | None:
    """Resolve `host` ONCE and return the address we will connect to.

    Returns None — refusing the fetch entirely — when resolution fails or
    when *any* address in the answer is not globally routable. Rejecting on
    "any" rather than "all" matters: a hostile resolver that returns
    `[93.184.216.34, 127.0.0.1]` must not be able to get a private address
    into the answer set at all.

    The caller connects to the returned literal, so there is no second
    resolution and therefore no DNS-rebinding window between check and
    connect.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return None

    addresses: list[str] = []
    for family, _socktype, _proto, _canonname, sockaddr in infos:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        if not sockaddr:
            continue
        addresses.append(str(sockaddr[0]))

    if not addresses:
        return None
    for address in addresses:
        if not _ip_is_publicly_routable(address):
            return None
    return addresses[0]


def _host_is_allowed(host: str) -> bool:
    """True if `host` is on the explicit provider allow-list.

    IP validation is no longer folded in here — it moved to
    `_resolve_pinned_ip`, which both validates AND hands back the address
    the request is pinned to. Keeping the two separate is what closes the
    resolve-then-reresolve window.
    """
    return bool(host) and host.lower() in _ALLOWED_HOSTS


# ---------------------------------------------------------------------------
# Per-host throttle
#
# A backfill of ~250 datasheets concentrates on a few dozen vendor domains
# (vishay, ti, we-online, …). Without a gate the sweep would issue every
# request for one host back to back, which is both rude and the fastest way
# to earn a vendor-side block. The map is process-local; the backfill runs
# in a single cron sidecar process so that is sufficient.
# ---------------------------------------------------------------------------
_HOST_THROTTLE_LOCK = threading.Lock()
_HOST_LAST_REQUEST_AT: dict[str, float] = {}
_HOST_THROTTLE_MAX_ENTRIES = 512


def _throttle_host(host: str) -> None:
    """Block until at least the configured gap has passed for `host`."""
    min_interval = float(settings().ASSET_FETCH_MIN_HOST_INTERVAL_SECONDS)
    if min_interval <= 0 or not host:
        return

    while True:
        with _HOST_THROTTLE_LOCK:
            now = time.monotonic()
            last = _HOST_LAST_REQUEST_AT.get(host)
            if last is None or (now - last) >= min_interval:
                if len(_HOST_LAST_REQUEST_AT) >= _HOST_THROTTLE_MAX_ENTRIES:
                    # Unbounded growth is the only failure mode here; drop
                    # the whole map rather than carry an LRU for a dict that
                    # tops out at a few dozen real entries.
                    _HOST_LAST_REQUEST_AT.clear()
                _HOST_LAST_REQUEST_AT[host] = now
                return
            wait_for = min_interval - (now - last)
        time.sleep(min(wait_for, min_interval))


def reset_host_throttle() -> None:
    """Clear the per-host throttle state. Test seam."""
    with _HOST_THROTTLE_LOCK:
        _HOST_LAST_REQUEST_AT.clear()


def _build_target(url: str, *, allow_list_required: bool) -> tuple[_FetchTarget | None, str | None]:
    """Validate `url` and resolve it to a pinned connection target.

    Returns `(target, None)` on success or `(None, failure_code)` on
    refusal. Every refusal path here happens BEFORE any socket is opened.

    Surrounding whitespace is stripped here rather than by callers: the
    datasheet backfill keys its records on the custom-field value verbatim,
    so it must be able to hand us a padded value without normalising it
    first (a normalised copy would never match the row it came from).
    """
    url = (url or "").strip()
    if not url:
        return None, "invalid_url"
    try:
        parsed = urlparse(url)
    except ValueError:
        return None, "invalid_url"

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return None, "invalid_url"

    # `user:pass@host` — refuse rather than forward whatever the credential
    # is to an arbitrary third-party host.
    if parsed.username or parsed.password:
        return None, "credentials_in_url"

    host = (parsed.hostname or "").lower()
    if not host:
        return None, "invalid_url"

    if allow_list_required:
        if not _host_is_allowed(host):
            log.warning("remote asset rejected: host not allow-listed (%s)", host)
            return None, "host_not_allowed"
    elif scheme != "https":
        # The unrestricted path is HTTPS-only: without the allow-list, TLS
        # certificate verification is what proves we reached the host the
        # URL named.
        log.warning("remote asset rejected: plaintext http on unrestricted path (%s)", host)
        return None, "scheme_not_https"

    try:
        port = parsed.port
    except ValueError:
        return None, "invalid_url"
    effective_port = port or _DEFAULT_PORT_BY_SCHEME[scheme]

    ip = _resolve_pinned_ip(host, effective_port)
    if ip is None:
        log.warning("remote asset rejected: host did not resolve to a public IP (%s)", host)
        return None, "ip_not_public"

    ip_literal = f"[{ip}]" if ":" in ip else ip
    authority = f"{ip_literal}:{port}" if port else ip_literal
    request_url = urlunparse(
        (
            scheme,
            authority,
            parsed.path or "/",
            parsed.params,
            parsed.query,
            "",  # fragments are never sent on the wire
        )
    )
    host_header = f"{host}:{port}" if port else host
    return (
        _FetchTarget(
            request_url=request_url,
            host_header=host_header,
            sni_hostname=host,
            ip=ip,
            redacted_ref=_redact(url),
        ),
        None,
    )


def _http_get(target: _FetchTarget) -> _AssetResponse:
    """Network seam — patched by tests. Streams the response body and
    aborts as soon as the running byte total exceeds `_MAX_BYTES`,
    so a hostile multi-GB body can never be fully buffered into memory
    before the size cap fires (security #285).

    The request goes to `target.request_url`, whose authority is the IP
    literal validated by `_resolve_pinned_ip`. The `Host:` header and the
    `sni_hostname` extension carry the original hostname, so vhost routing
    works and — critically — httpx still verifies the server certificate
    against the real hostname (httpcore passes `sni_hostname` straight
    through as `server_hostname` to `ssl.wrap_socket`). TLS verification is
    never relaxed anywhere on this path — see ADR-0008.

    `follow_redirects=False` is load-bearing: a 30x upstream is treated
    as a refusal (returns the redirect itself, which the caller rejects
    because `status_code != 200`). Auto-following would void the pinned-IP
    guarantee since the Location: header could point anywhere.

    The returned `_AssetResponse.body` is `None` when the download was
    aborted because of the size cap (either Content-Length pre-check or
    mid-stream chunk-counter); the caller treats that the same as any
    other refusal.
    """
    ref = target.redacted_ref
    started_at = time.monotonic()
    with httpx.Client(timeout=_TIMEOUT_SEC, follow_redirects=False) as client:
        with client.stream(
            "GET",
            target.request_url,
            headers={"Host": target.host_header},
            extensions={"sni_hostname": target.sni_hostname},
        ) as resp:
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
                            "remote asset rejected: Content-Length %s > %d (%s)",
                            cl,
                            _MAX_BYTES,
                            ref,
                        )
                        return _AssetResponse(status, headers, None)
                except ValueError:
                    pass

            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_bytes(chunk_size=_CHUNK_SIZE):
                # Wall-clock budget. Checked per chunk because that is the
                # only place a slow-dribble server gives us control back —
                # its per-read timer is reset by every byte it sends.
                if (time.monotonic() - started_at) > _MAX_WALL_CLOCK_SEC:
                    raise _WallClockExceeded(ref)
                total += len(chunk)
                if total > _MAX_BYTES:
                    log.warning(
                        "remote asset rejected: streamed body exceeded %d bytes (%s)",
                        _MAX_BYTES,
                        ref,
                    )
                    return _AssetResponse(status, headers, None)
                chunks.append(chunk)

            return _AssetResponse(status, headers, b"".join(chunks))


def _write_content_addressed(body: bytes, workspace_id: str, ext: str) -> tuple[str, str]:
    """Write `body` to `{UPLOAD_DIR}/parts/{ws}/{sha}.{ext}`.

    Returns `(filename, storage_key)`. `storage_key` is relative to
    UPLOAD_DIR, which is the form `attachments.storage_key` stores.
    """
    sha = hashlib.sha256(body).hexdigest()
    filename = f"{sha}.{ext}"
    storage_key = os.path.join("parts", str(workspace_id), filename)

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
    return filename, storage_key


def fetch_asset(
    url: str,
    workspace_id: str,
    kind: str,
    *,
    allow_any_host: bool = False,
) -> AssetFetchResult:
    """Download `url` into this workspace's content-addressed asset store.

    The unrestricted policy needs BOTH conditions: `kind` in
    `_UNRESTRICTED_KINDS` AND the caller explicitly passing
    `allow_any_host=True`. Everything else keeps the host allow-list.

    The opt-in is deliberate. Only the `datasheet-backfill` cron job passes
    it. The request-path callers (provider import, provider refresh) do NOT,
    so a user-triggered request can never make the backend open a connection
    to a host outside the allow-list — and can never be made to block the
    single uvicorn worker (ADR-0012) for a 10s timeout plus a per-host
    throttle against an arbitrary vendor. Those parts get their datasheet
    within the hour from the sidecar instead. See ADR-0033.

    Never raises — every refusal is reported as an `AssetFetchResult` with
    a stable `failure_code`, so a caller sweeping hundreds of URLs can
    record the reason and move on.
    """
    unrestricted = allow_any_host and kind in _UNRESTRICTED_KINDS
    target, failure = _build_target(url, allow_list_required=not unrestricted)
    if target is None:
        return AssetFetchResult(failure_code=failure)

    # Throttle ONLY on the relaxed path. `_throttle_host` is a process-global
    # `time.sleep` keyed on hostname, and the allow-listed callers run inside
    # request handlers: bulk-import-from-scan pulls up to 50 images from the
    # SAME provider CDN host inside one request with a 60s deadline, and
    # refresh-from-provider is a sync route on a single uvicorn worker whose
    # thread pool would fill with sleepers. Serialising either at 2s a piece
    # is a self-inflicted outage. The backfill is the only caller that both
    # needs the courtesy gap and can afford to wait for it.
    if unrestricted:
        _throttle_host(target.sni_hostname)

    try:
        resp = _http_get(target)
    except _WallClockExceeded:
        log.warning("remote asset rejected: wall-clock budget exceeded (%s)", target.redacted_ref)
        return AssetFetchResult(failure_code="too_slow")
    except httpx.TimeoutException:
        return AssetFetchResult(failure_code="timeout")
    except Exception:
        log.warning("remote asset fetch failed (%s)", target.redacted_ref, exc_info=True)
        return AssetFetchResult(failure_code="network_error")

    # 30x is treated as a refusal — we don't follow redirects (see
    # `_http_get` docstring).
    if 300 <= resp.status_code < 400:
        return AssetFetchResult(failure_code="redirect_refused")
    if resp.status_code != 200:
        return AssetFetchResult(failure_code=f"http_{resp.status_code}")
    # `body is None` signals the streaming guard aborted because the
    # response exceeded `_MAX_BYTES` (see `_http_get`).
    if resp.body is None:
        return AssetFetchResult(failure_code="too_large")
    body = resp.body
    if not body:
        return AssetFetchResult(failure_code="empty_body")

    ext = _ext_from_response(resp.headers, url)

    # A datasheet is a PDF. Without the host allow-list a vendor URL that
    # 200s with an HTML landing page (or anything else) would otherwise land
    # on disk as an opaque `.bin` for every part. Refuse instead: the caller
    # keeps the upstream URL, and the backfill records `unexpected_type`
    # rather than filling the store with junk.
    if unrestricted and ext != "pdf":
        log.warning(
            "remote asset rejected: datasheet is not a PDF (ext=%s) from %s",
            ext,
            target.redacted_ref,
        )
        return AssetFetchResult(failure_code="unexpected_type")

    # Magic-byte validation (SEC2-012). Skip check for opaque .bin fallback —
    # those already carry a forced-download Content-Disposition when served.
    if ext != "bin":
        sniffed = _sniff_ext(body[:16])
        if sniffed != ext:
            log.warning(
                "remote asset rejected: magic bytes (%s) do not match "
                "declared extension (%s) from %s",
                sniffed or "<unknown>",
                ext,
                target.redacted_ref,
            )
            return AssetFetchResult(failure_code="magic_mismatch")

    try:
        filename, storage_key = _write_content_addressed(body, workspace_id, ext)
    except OSError:
        log.exception("remote asset write failed (%s)", target.redacted_ref)
        return AssetFetchResult(failure_code="write_error")

    return AssetFetchResult(
        stored=StoredAsset(
            # Public URL — served by GET /api/parts/assets/{ws_id}/{filename}.
            public_url=f"/api/parts/assets/{workspace_id}/{filename}",
            storage_key=storage_key,
            filename=filename,
            sha256=filename.rsplit(".", 1)[0],
            ext=ext,
            size_bytes=len(body),
            mime_type=mime_for_ext(ext),
        )
    )


def fetch_provider_asset(url: str, workspace_id: str, kind: str) -> str | None:
    """Back-compat wrapper: the public asset path, or None on any refusal.

    Kept because the provider-import and provider-refresh paths only care
    about "did we localise it"; callers that need the failure reason or the
    storage key (the datasheet backfill) use `fetch_asset` directly.
    """
    stored = fetch_asset(url, workspace_id, kind).stored
    return stored.public_url if stored is not None else None
