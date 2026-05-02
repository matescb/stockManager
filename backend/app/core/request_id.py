"""Request-ID middleware and context-variable helpers.

BE2-012 / issue #61. Every inbound request gets a `request_id` (hex UUID)
minted and stored on `request.state.request_id`.  The same value is
propagated via:

- `X-Request-Id` response header (so the browser / curl caller can copy it)
- a `ContextVar` that `LogFilter` (see `core/logging.py`) injects into every
  log record emitted during that request's stack, so operators can grep the
  journal for a single id.
- `core/responses` error envelopes so the frontend can surface it on error
  modals.

If the caller sends an `X-Request-Id` header it is reused *if and only if*
it matches the allowed shape (1–64 hexadecimal characters, case-insensitive).
A malformed inbound value is silently replaced with a fresh one — never
rejected, never logged as an error.
"""
from __future__ import annotations

import re
import uuid
from contextvars import ContextVar
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Exported so logging.py can import it.
request_id_var: ContextVar[str | None] = ContextVar("request_id_var", default=None)

_VALID_REQUEST_ID = re.compile(r"^[0-9a-fA-F]{1,64}$")


def _mint_request_id(inbound: str | None) -> str:
    """Return the inbound id if it's a valid hex string (1–64 chars),
    otherwise mint a fresh one."""
    if inbound and _VALID_REQUEST_ID.match(inbound):
        return inbound[:64]
    return uuid.uuid4().hex


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Mint / reuse a request-id on every request.

    Must be registered *before* CORS / CSRF so the id is available even
    when those middlewares short-circuit the request.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = _mint_request_id(request.headers.get("x-request-id"))
        request.state.request_id = rid

        # Set contextvar so the log filter can inject it into every record
        # emitted while this coroutine / any sub-task is on the stack.
        token = request_id_var.set(rid)

        # Tag the Sentry event with the same id — free when DSN is unset.
        try:
            import sentry_sdk

            sentry_sdk.set_tag("request_id", rid)
        except Exception:  # noqa: BLE001 — never let Sentry break routing
            pass

        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)

        response.headers["X-Request-Id"] = rid
        return response
