"""The ASGI wrapper that authenticates every request to `/mcp`.

This is the MCP surface's whole authentication story. It sits between
Starlette's middleware stack and the SDK's session manager, so the SDK
never sees an unauthenticated byte and no tool has to remember to check.

Why a raw ASGI wrapper rather than a FastAPI dependency: the MCP app is
mounted as an opaque sub-application. It owns its own routing and its
own streaming response lifecycle, so there is no endpoint signature to
hang `Depends(...)` off. What there is, is a scope — and everything the
existing token path needs (`Authorization`, the client address, a place
to put `request.state`) can be read off one.

Three rules this surface does NOT share with `/api`:

* **No cookie fallback, ever.** A session cookie alone gets the same 401
  as no credential at all. `/mcp` is a machine surface; the browser
  session is the human one, and letting a page on another origin drive
  tools by riding a logged-in cookie is precisely the hole the CSRF
  middleware exists to close. Refusing the cookie is what makes that
  middleware irrelevant here rather than merely bypassed.
* **`read_only` authenticates.** The HTTP path refuses a read-only token
  any non-GET, which would reject every JSON-RPC call including
  `tools/list`. Here the credential is established first and the write
  question is asked per tool, so a read-only token still connects, still
  discovers the full tool list, and is refused only when it calls a tool
  that writes. See `tools/_registry.py::require_write`.
* **One body for every failure.** Same reasoning as ADR-0029's
  `_invalid_token`: no oracle distinguishing "no such token" from
  "revoked" from "you were removed from the workspace".
"""
from __future__ import annotations

import json
import logging

import anyio.to_thread
from fastapi import HTTPException
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core import deps
from app.core.errors import ErrorCodes
from app.domain.workspaces.models import Workspace
from app.mcp.principal import Principal, bind

_log = logging.getLogger(__name__)

# JSON-RPC has no transport-level notion of "unauthorized", and a client
# that gets a bare 401 with an HTML body reports it as a protocol
# violation rather than as "your token is wrong". So the body is a
# well-formed JSON-RPC error object AND the status is 401: the SDK
# surfaces the `message` to the user, and a plain `curl` still sees a
# status a human recognises.
#
# `id: null` is correct here and not a placeholder — the request was
# rejected before its body was read, so its id is genuinely unknown.
# -32001 is in the implementation-defined server-error range; the
# protocol reserves nothing for authentication.
_UNAUTHORIZED_CODE = -32001

_UNAUTHORIZED_BODY = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": None,
        "error": {
            "code": _UNAUTHORIZED_CODE,
            "message": "invalid api token",
            "data": {"code": ErrorCodes.AUTH_INVALID_TOKEN},
        },
    }
).encode("utf-8")


async def _reject(send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_UNAUTHORIZED_BODY)).encode("ascii")),
                # Names the scheme a client should retry with. Both
                # `Token` and `Bearer` are accepted (deps.py), but only
                # one can be advertised and `Bearer` is the registered
                # one.
                (b"www-authenticate", b'Bearer realm="mcp"'),
                (b"x-content-type-options", b"nosniff"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _UNAUTHORIZED_BODY})


def authenticate(request: Request) -> Principal | None:
    """Verify the request's bearer credential. None on any failure.

    Runs in its own session, committed and closed before the tool's
    session opens. That session exists for the token's `last_used_at`
    telemetry, which `resolve_live_token` commits on its own — see the
    comment there about why it must not ride the request transaction.
    """
    from app.infra.db import SessionLocal

    header = request.headers.get("Authorization")
    if not header:
        return None
    scheme, _, raw = header.partition(" ")
    if scheme.lower() not in ("token", "bearer"):
        return None
    raw = raw.strip()
    if not raw:
        return None

    db = SessionLocal()
    try:
        # `resolve_live_token` is the shared half of the HTTP token path:
        # resolve, owner lookup, workspace-membership re-check, throttled
        # telemetry. It deliberately stops short of the read-only gate,
        # which is method-based and meaningless here.
        user, token = deps.resolve_live_token(request, db, raw)
        ws = db.get(Workspace, token.workspace_id)
        if ws is None:
            return None
        role = deps.membership_role(db, user, ws)
        # `resolve_live_token` commits its own throttled telemetry, so
        # this is a no-op on most requests. Kept so the session is never
        # closed with anything outstanding.
        db.commit()
        # Read every attribute we need BEFORE the commit and the close
        # below. `resolve_live_token` parks the live `ApiToken` row on
        # `request.state.api_token` for its HTTP callers, and that row
        # outlives this session — which only works because `SessionLocal`
        # is built with `expire_on_commit=False` (`infra/db.py:41`), so
        # a committed instance keeps its loaded values instead of
        # expiring and re-querying on a closed session. Nothing here
        # relies on that (ids are copied into the frozen `Principal`),
        # and nothing should: if that flag ever changes, this function
        # keeps working and the HTTP surfaces are where to look.
        return Principal(
            user_id=user.id,
            workspace_id=ws.id,
            token_id=token.id,
            role=role,
            request_id=getattr(request.state, "request_id", None),
        )
    except HTTPException:
        # Every rejection reason collapses to one 401 upstream, so there
        # is nothing to inspect here.
        db.rollback()
        return None
    except Exception:
        # A DB outage must not read as a bad credential to the operator
        # reading logs, even though the client sees the same 401.
        _log.exception("mcp authentication failed unexpectedly")
        db.rollback()
        return None
    finally:
        db.close()


class McpAuthMiddleware:
    """Authenticate, bind the principal, delegate to the MCP app.

    A pure-ASGI callable rather than a `BaseHTTPMiddleware` subclass:
    the latter buffers the response through a queue, which breaks the
    streamed `text/event-stream` body the transport depends on.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope)
        # `authenticate` is synchronous and hits the database ~5 times
        # (token, user, membership, workspace, role) plus a throttled
        # telemetry commit. Running that inline would block the event
        # loop for the whole round trip — and prod runs ONE uvicorn
        # worker, so it would stall every `/api` request in flight, not
        # just this one. Offloaded to a worker thread, which is also
        # where the SDK runs the tools themselves.
        principal = await anyio.to_thread.run_sync(authenticate, request)
        if principal is None:
            await _reject(send)
            return
        # Downstream telemetry (request logging, Sentry tags) reads the
        # tenant off request state exactly as it does for HTTP routes.
        request.state.workspace_id = str(principal.workspace_id)
        request.state.user_id = str(principal.user_id)
        with bind(principal):
            await self.app(scope, receive, send)
