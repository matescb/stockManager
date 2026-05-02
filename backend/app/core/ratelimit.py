"""Shared SlowAPI Limiter instance.

slowapi's decorators carry a reference to the Limiter, and the per-request
middleware reads `request.app.state.limiter`. Both must be the same object
for the bucket store to be consistent — putting the instance here means the
import order in main.py / auth.py doesn't matter and there's no risk of two
parallel buckets.

Disabled outside prod so the test suite (and local dev) can hammer endpoints
without burning through the limit. The decorators stay in place either way
so the wiring is always exercised.
"""
from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings


limiter = Limiter(
    key_func=get_remote_address,
    enabled=settings().APP_ENV == "prod",
)


def workspace_key(request: Request) -> str:
    """Rate-limit key that buckets by workspace rather than IP.

    Preferred over IP-only keying for provider-fanout endpoints and the
    search endpoint because members in the same corporate NAT share one IP
    but each workspace's paid API quota is independent.

    Resolution order:
    1. request.state.workspace_id — set by get_current_workspace in deps.py
       for authenticated routes that already resolved the workspace. Any
       endpoint using this key_func MUST depend on get_current_workspace
       (or otherwise populate request.state.workspace_id from a verified
       token) — never trust raw client-supplied headers/cookies for the
       bucket id, since a client could rotate that value to fragment the
       bucket and bypass the limit.
    2. Remote address — fallback for unauthenticated paths, startup probes,
       etc. Safe because rate limiting is disabled outside prod.
    """
    ws_id = getattr(request.state, "workspace_id", None)
    if ws_id:
        return f"ws:{ws_id}"

    return get_remote_address(request)
