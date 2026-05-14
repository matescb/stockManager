"""Sentry envelope tunnel.

The Sentry React SDK normally POSTs directly to `*.ingest.sentry.io`, which
is blocked by many ad-blockers (uBlock Origin, Brave Shields, Pi-hole). The
recommended workaround is `Sentry.init({ tunnel: "/api/sentry-tunnel" })`:
the browser sends a same-origin request that we forward upstream.

Reference: https://docs.sentry.io/platforms/javascript/troubleshooting/#using-the-tunnel-option

Security posture:
* Same-origin browser SDK posts are allowed pre-auth so login-screen errors
  still reach Sentry. Requests without a trusted Origin must carry a valid
  session cookie before we do any tunnel work.
* Host allow-list against `SENTRY_DSN` / `VITE_SENTRY_DSN`. Without it,
  this endpoint would be an open egress to anywhere Sentry-shaped — we
  cap it to our own DSN's host + project id.
* Rate-limited to 30/min/IP (Sec CRIT-5). Real Sentry SDKs do their own
  client-side rate limiting and never hit this; the limit only catches
  abuse, not legitimate traffic.
* Body cap at SENTRY_TUNNEL_MAX_BYTES (200 KiB default). Envelopes are
  streamed and the running byte count is checked per chunk, so an
  oversize body is rejected before the full payload buffers in RAM.
"""
from __future__ import annotations

import json
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Request, Response, status

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.errors import ErrorCodes, raise_http
from app.core.ratelimit import limiter
from app.infra import db as infra_db

router = APIRouter()

SENTRY_TUNNEL_MAX_CHUNKS = 1024


def _parse_allowed_endpoints(*dsns: str) -> tuple[tuple[str, str], ...]:
    """Return the (host, project_id) pairs envelopes may target.

    The tunnel exists for the React SDK, so VITE_SENTRY_DSN is the
    primary entry. SENTRY_DSN (the backend project's DSN) is included
    too for shops that point both runtimes at the same project, and for
    forwarding any backend-emitted envelopes that ever route through here.
    An empty string skips that slot.
    """
    out: set[tuple[str, str]] = set()
    for dsn in dsns:
        if not dsn:
            continue
        parsed = urlparse(dsn)
        host = parsed.hostname
        project_id = parsed.path.strip("/")
        if host and project_id:
            out.add((host, project_id))
    return tuple(sorted(out))


_cfg = settings()
ALLOWED_ENDPOINTS = _parse_allowed_endpoints(_cfg.VITE_SENTRY_DSN, _cfg.SENTRY_DSN)


async def _read_bounded_envelope(request: Request, max_bytes: int) -> bytes:
    body = bytearray()
    chunk_count = 0
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        chunk_count += 1
        if chunk_count > SENTRY_TUNNEL_MAX_CHUNKS:
            raise_http(
                status.HTTP_413_CONTENT_TOO_LARGE,
                ErrorCodes.SENTRY_TUNNEL_TOO_LARGE,
                "envelope has too many chunks",
                max_chunks=SENTRY_TUNNEL_MAX_CHUNKS,
            )

        total += len(chunk)
        if total > max_bytes:
            raise_http(
                status.HTTP_413_CONTENT_TOO_LARGE,
                ErrorCodes.SENTRY_TUNNEL_TOO_LARGE,
                f"envelope exceeds {max_bytes} bytes",
                max_bytes=max_bytes,
            )
        body.extend(chunk)
    return bytes(body)


def _origin_host(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlparse(value)
    except Exception:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _has_trusted_origin(request: Request) -> bool:
    origin = _origin_host(request.headers.get("origin"))
    if origin is None:
        return False
    allowed = {
        host
        for host in (_origin_host(candidate) for candidate in settings().cors_origin_list)
        if host
    }
    return origin in allowed


def _require_session_or_trusted_origin(request: Request) -> None:
    if _has_trusted_origin(request):
        return

    if not request.cookies.get(settings().SESSION_COOKIE_NAME):
        raise_http(
            status.HTTP_401_UNAUTHORIZED,
            ErrorCodes.AUTH_NOT_AUTHENTICATED,
            "not authenticated",
        )

    db = infra_db.SessionLocal()
    try:
        get_current_user(request, db)
    finally:
        db.close()


@router.post("/sentry-tunnel")
@limiter.limit("30/minute")
async def sentry_tunnel(request: Request) -> Response:
    _require_session_or_trusted_origin(request)

    allowed = ALLOWED_ENDPOINTS
    if not allowed:
        # No DSN on the server — there's nothing to forward to. Return a
        # soft 204 so SDK retries don't hammer the route.
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Streaming-bounded body read. `await request.body()` would buffer
    # the entire payload in RAM with no upper bound — a single curl loop
    # could pump arbitrary bytes through this worker. Iterating the
    # stream lets us 413 the moment we cross the cap.
    max_bytes = settings().SENTRY_TUNNEL_MAX_BYTES
    envelope = await _read_bounded_envelope(request, max_bytes)

    if not envelope:
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            ErrorCodes.SENTRY_TUNNEL_EMPTY,
            "empty envelope",
        )

    # The first line of every Sentry envelope is a JSON header carrying
    # the DSN the client believes it's sending to. Validate it against
    # the server's allow-list so this tunnel only ever forwards to a
    # Sentry project we explicitly configured.
    header_line, _, _ = envelope.partition(b"\n")
    try:
        header = json.loads(header_line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            ErrorCodes.SENTRY_TUNNEL_MALFORMED_HEADER,
            "malformed envelope header",
        )
    client_dsn = header.get("dsn")
    if not client_dsn:
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            ErrorCodes.SENTRY_TUNNEL_MISSING_DSN,
            "envelope header missing dsn",
        )
    parsed = urlparse(client_dsn)
    target_host = parsed.hostname
    target_project = parsed.path.strip("/")
    if (target_host, target_project) not in allowed:
        raise_http(
            status.HTTP_403_FORBIDDEN,
            ErrorCodes.SENTRY_TUNNEL_DSN_MISMATCH,
            "dsn mismatch",
        )

    upstream = f"https://{target_host}/api/{target_project}/envelope/"
    # 10s is more than enough for an ingest POST; longer would let a hung
    # upstream tie up our worker.
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            upstream,
            content=envelope,
            headers={"Content-Type": "application/x-sentry-envelope"},
        )
    # Forward Sentry's response so the SDK sees the real outcome (rate
    # limits, errors). Body is small JSON.
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )
