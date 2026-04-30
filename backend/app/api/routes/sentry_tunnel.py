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
* Host allow-list against `SENTRY_DSN`. Without it, this endpoint would be
  an open egress to anywhere Sentry-shaped — we cap it to our own DSN's
  host + project id.
* No rate-limit. Sentry SDKs do their own rate-limiting client-side.
"""
from __future__ import annotations

import json
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status

from app.core.config import settings


router = APIRouter()


def _allowed_endpoint() -> tuple[str, str] | None:
    """Parse SENTRY_DSN once at request time. Returns (host, project_id) or
    None if no DSN is configured (in which case the endpoint refuses)."""
    dsn = settings().SENTRY_DSN
    if not dsn:
        return None
    parsed = urlparse(dsn)
    host = parsed.hostname
    project_id = parsed.path.strip("/")
    if not host or not project_id:
        return None
    return host, project_id


@router.post("/sentry-tunnel")
async def sentry_tunnel(request: Request) -> Response:
    allowed = _allowed_endpoint()
    if allowed is None:
        # No SENTRY_DSN on the server — there's nothing to forward to.
        # Return a soft 204 so SDK retries don't hammer the route.
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    expected_host, expected_project = allowed

    envelope = await request.body()
    if not envelope:
        raise HTTPException(status_code=400, detail="empty envelope")

    # The first line of every Sentry envelope is a JSON header carrying
    # the DSN the client believes it's sending to. Validate it against the
    # server-configured DSN so this tunnel only ever forwards to our own
    # Sentry project.
    header_line, _, _ = envelope.partition(b"\n")
    try:
        header = json.loads(header_line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="malformed envelope header")
    client_dsn = header.get("dsn")
    if not client_dsn:
        raise HTTPException(status_code=400, detail="envelope header missing dsn")
    parsed = urlparse(client_dsn)
    if parsed.hostname != expected_host or parsed.path.strip("/") != expected_project:
        raise HTTPException(status_code=403, detail="dsn mismatch")

    upstream = f"https://{expected_host}/api/{expected_project}/envelope/"
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
