"""Sentry envelope tunnel.

The Sentry React SDK normally POSTs directly to `*.ingest.sentry.io`, which
is blocked by many ad-blockers (uBlock Origin, Brave Shields, Pi-hole). The
recommended workaround is `Sentry.init({ tunnel: "/api/sentry-tunnel" })`:
the browser sends a same-origin request that we forward upstream.

Reference: https://docs.sentry.io/platforms/javascript/troubleshooting/#using-the-tunnel-option

Security posture:
* No auth gate — Sentry's SDK is unauthenticated by design (the public DSN
  is the identifier, ingest.sentry.io accepts any envelope tagged with a
  valid project key).
* Host allow-list against `SENTRY_DSN` / `VITE_SENTRY_DSN`. Without it,
  this endpoint would be an open egress to anywhere Sentry-shaped — we
  cap it to our own DSN's host + project id.
* Rate-limited to 60/min/IP (Sec CRIT-5). Real Sentry SDKs do their own
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
from app.core.errors import ErrorCodes, raise_http
from app.core.ratelimit import limiter

router = APIRouter()


def _allowed_endpoints() -> list[tuple[str, str]]:
    """Parse the configured DSN(s) once per request. Returns the list of
    (host, project_id) tuples that envelopes are allowed to target.

    The tunnel exists for the React SDK, so VITE_SENTRY_DSN is the
    primary entry. SENTRY_DSN (the backend project's DSN) is included
    too for shops that point both runtimes at the same project, and for
    forwarding any backend-emitted envelopes that ever route through
    here. An empty string skips that slot."""
    cfg = settings()
    out: list[tuple[str, str]] = []
    for dsn in (cfg.VITE_SENTRY_DSN, cfg.SENTRY_DSN):
        if not dsn:
            continue
        parsed = urlparse(dsn)
        host = parsed.hostname
        project_id = parsed.path.strip("/")
        if host and project_id:
            out.append((host, project_id))
    return out


@router.post("/sentry-tunnel")
@limiter.limit("60/minute")
async def sentry_tunnel(request: Request) -> Response:
    allowed = _allowed_endpoints()
    if not allowed:
        # No DSN on the server — there's nothing to forward to. Return a
        # soft 204 so SDK retries don't hammer the route.
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Streaming-bounded body read. `await request.body()` would buffer
    # the entire payload in RAM with no upper bound — a single curl loop
    # could pump arbitrary bytes through this worker. Iterating the
    # stream lets us 413 the moment we cross the cap.
    max_bytes = settings().SENTRY_TUNNEL_MAX_BYTES
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise_http(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                ErrorCodes.SENTRY_TUNNEL_TOO_LARGE,
                f"envelope exceeds {max_bytes} bytes",
                max_bytes=max_bytes,
            )
        chunks.append(chunk)
    envelope = b"".join(chunks)

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
