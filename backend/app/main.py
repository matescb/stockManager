from __future__ import annotations

import os
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.logging import configure_logging

# Configure structured logging once at import time, before anything else
# tries to log. uvicorn replays logs through the root logger so this
# captures its lifecycle messages too.
configure_logging()


# Top-level (testable) Sentry event scrubber. Runs as `before_send` so
# the event is mutated before it leaves the worker.
#
# Default-deny `request.data` on any non-GET method. The previous narrow
# allow-list ("only /api/workspaces") was identified by the 2026-05-01 v2
# teardown (SEC2-005) as leaking on every other route that handles a
# secret-bearing body:
#   - POST /api/auth/signup, /login → plaintext password
#   - POST /api/invitations/accept → raw invitation token (bearer-equivalent)
#   - POST /api/parts/lookup-mpn → decrypted provider API keys in scope
#   - POST /api/parts/bulk-import-from-scan → same
#   - PATCH /api/workspaces/current → plaintext provider/scanner secrets
#   - POST /api/attachments → multipart bodies with arbitrary user content
# A 5xx in any of those handlers would attach the request body. Replacing
# the URL allow-list with a method default-deny is the only way to keep
# this safe as new routes are added — there is no read-only POST in the
# API today, and any future "just status" body still has nothing useful
# for triage that isn't already in URL + status_code.
#
# Cookie / Authorization / X-Workspace-Id headers are tenant- or
# session-identifying on every method, so the header scrub runs first and
# applies regardless of method.
def _scrub_event(event, _hint):
    request = event.get("request")
    if not isinstance(request, dict):
        return event
    headers = request.get("headers")
    if isinstance(headers, dict):
        drop = {"cookie", "authorization", "x-workspace-id"}
        for k in list(headers.keys()):
            if k.lower() in drop:
                headers.pop(k, None)
    method = (request.get("method") or "").upper()
    if method and method != "GET":
        if request.pop("data", None) is not None:
            request["body_redacted"] = True
    return event


# Initialise Sentry BEFORE FastAPI is imported, per the SDK's docs — its
# auto-instrumentation patches starlette / FastAPI on first import. No-ops
# entirely when SENTRY_DSN is empty (dev / unset prod), so this stays
# zero-cost outside production.
def _init_sentry() -> None:
    from app.core.config import settings  # local import to avoid cycle
    cfg = settings()
    if not cfg.SENTRY_DSN:
        return
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration
    sentry_sdk.init(
        dsn=cfg.SENTRY_DSN,
        environment=cfg.APP_ENV,
        release=cfg.SENTRY_RELEASE or None,
        traces_sample_rate=cfg.SENTRY_TRACES_SAMPLE_RATE,
        # `send_default_pii=True` ships user IP + request headers + body.
        # Combined with the `before_send` scrubber below, this is the
        # right balance: triage gets enough context, plaintext credentials
        # never reach Sentry.
        send_default_pii=True,
        # Drop frame-local variables from event payloads. Default-True
        # combined with `send_default_pii=True` was shipping locals like
        # `payload.password`, `decrypt(...)` return values, and Pydantic
        # model `__init__` kwargs that include API keys (v2 teardown
        # SEC2-005). Stack traces alone are enough triage signal; locals
        # are an unbounded leak surface.
        include_local_variables=False,
        before_send=_scrub_event,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
        ],
    )


_init_sentry()

from app.api.routes import (
    attachments,
    auth,
    bom_presets,
    builds,
    catalog,
    custom_fields,
    invitations,
    lots,
    orders,
    parts,
    parts_provider,
    projects,
    reports,
    search,
    sentry_tunnel,
    stock,
    storage,
    tags,
    workspaces,
)
from app.core.config import settings
from app.core.deps import require_member_for_writes
from app.core.responses import http_exception_handler, validation_exception_handler

_is_prod = settings().APP_ENV == "prod"

app = FastAPI(
    title="Parts Inventory & Production Manager",
    version="0.1.0",
    # Disable OpenAPI surface in prod — the schema is a free attacker
    # roadmap to every endpoint, parameter shape, and response. Kept on
    # in dev where the interactive playground is useful.
    docs_url=None if _is_prod else "/docs",
    redoc_url=None if _is_prod else "/redoc",
    openapi_url=None if _is_prod else "/openapi.json",
)

# Rate limiter wired before CORS so 429 responses still get the right headers.
from slowapi import _rate_limit_exceeded_handler  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402

from app.core.ratelimit import limiter  # noqa: E402

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# SEC2-001 — CSRF Origin/Referer guard. Cookie auth is otherwise wide
# open to a malicious page on another origin issuing a state-changing
# request that rides the victim's session cookie (the SameSite=Lax
# attribute does not block top-level POSTs). We don't implement a
# double-submit token: comparing the request's `Origin` (or `Referer`
# fallback) against the configured CORS allow-list is sufficient
# because browsers attach those headers automatically on any
# cross-origin request and a malicious page cannot forge them.
_CSRF_PROTECTED_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})

# Routes that are deliberately reachable cross-origin or pre-auth.
# - /api/sentry-tunnel: Sentry's SDK is unauthenticated by design and
#   the host allow-list inside the route is the actual gate.
# - /api/auth/login + /signup: not yet authenticated; the threat
#   here is brute force, handled by slowapi rate limiting.
_CSRF_EXEMPT_PATHS = frozenset({
    "/api/sentry-tunnel",
    "/api/auth/login",
    "/api/auth/signup",
})


def _origin_host(value: str | None) -> str | None:
    """Return scheme://host[:port] for a value that may be an Origin
    header (already in that form) or a Referer URL (full URL with
    path). Returns None on parse failure."""
    if not value:
        return None
    try:
        parsed = urlparse(value)
    except Exception:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


class CsrfOriginMiddleware(BaseHTTPMiddleware):
    """Reject state-changing requests whose Origin / Referer doesn't
    match the configured allow-list. GET / HEAD / OPTIONS pass through
    untouched."""

    def __init__(self, app, allowed_origins: list[str], exempt_paths: frozenset[str]):
        super().__init__(app)
        # Normalise to scheme+host on init so the fast path is a single
        # set lookup per request.
        self._allowed = frozenset(
            o for o in (_origin_host(x) for x in allowed_origins) if o
        )
        self._exempt = exempt_paths

    async def dispatch(self, request: Request, call_next):
        if request.method not in _CSRF_PROTECTED_METHODS:
            return await call_next(request)
        if request.url.path in self._exempt:
            return await call_next(request)
        origin = _origin_host(request.headers.get("origin")) or _origin_host(
            request.headers.get("referer")
        )
        if origin is None or origin not in self._allowed:
            return JSONResponse(
                status_code=403,
                content={
                    "data": None,
                    "status": {
                        "category": "forbidden",
                        "message": "cross-origin request blocked",
                    },
                },
            )
        return await call_next(request)


app.add_middleware(
    CsrfOriginMiddleware,
    allowed_origins=settings().cors_origin_list,
    exempt_paths=_CSRF_EXEMPT_PATHS,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)

os.makedirs(settings().UPLOAD_DIR, exist_ok=True)

_member_gate = [Depends(require_member_for_writes)]

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(workspaces.router, prefix="/api/workspaces", tags=["workspaces"])
app.include_router(parts.router, prefix="/api/parts", tags=["parts"], dependencies=_member_gate)
app.include_router(storage.router, prefix="/api/storage", tags=["storage"], dependencies=_member_gate)
app.include_router(stock.router, prefix="/api/stock", tags=["stock"], dependencies=_member_gate)
app.include_router(lots.router, prefix="/api/lots", tags=["lots"], dependencies=_member_gate)
app.include_router(projects.router, prefix="/api/projects", tags=["projects"], dependencies=_member_gate)
app.include_router(orders.router, prefix="/api/orders", tags=["orders"], dependencies=_member_gate)
app.include_router(builds.router, prefix="/api/builds", tags=["builds"], dependencies=_member_gate)
app.include_router(reports.router, prefix="/api/reports", tags=["reports"], dependencies=_member_gate)
app.include_router(bom_presets.router, prefix="/api/bom-presets", tags=["bom_presets"], dependencies=_member_gate)
app.include_router(invitations.router, prefix="/api/invitations", tags=["invitations"])
# Sentry tunnel: same-origin proxy for /api/sentry-tunnel to Sentry's
# ingest endpoint, so ad-blockers don't drop the SDK's events. NOT gated
# on workspace membership — the SDK fires from the login screen too.
app.include_router(sentry_tunnel.router, prefix="/api", tags=["sentry"])
app.include_router(attachments.router, prefix="/api/attachments", tags=["attachments"], dependencies=_member_gate)
app.include_router(custom_fields.router, prefix="/api/custom-fields", tags=["custom_fields"], dependencies=_member_gate)
app.include_router(tags.router, prefix="/api/tags", tags=["tags"], dependencies=_member_gate)
app.include_router(search.router, prefix="/api/search", tags=["search"], dependencies=_member_gate)
app.include_router(
    parts_provider.router,
    prefix="/api/parts",
    tags=["parts_provider"],
    dependencies=_member_gate,
)

# Public, token-gated read-only catalog. Mounted AFTER the /api routers and
# intentionally without a member-gate dependency.
app.include_router(catalog.router, prefix="/catalog", tags=["catalog"])


@app.get("/api/health")
def health():
    """Liveness + DB + uploads-volume check.

    Used by three callers:
    - the docker-compose backend healthcheck (controls when `web` starts,
      and what `docker compose ps` reports for status),
    - the post-deploy CI gate (`curl /api/health` retried after `docker
      compose up` so a failed migration / missing env / DB outage fails
      the deploy instead of returning a green CI result on a broken prod),
    - manual smoke after operator-driven changes.

    Returns 503 with structured detail when a check fails so the caller
    can distinguish `app not started yet` (connection refused) from
    `app up but DB unreachable`. INFRA2-002 / INFRA-001 / Infra HIGH-1.
    """
    from sqlalchemy import text

    from app.infra.db import get_engine

    db_ok = True
    db_detail: str | None = None
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1")).scalar_one()
    except Exception as e:  # noqa: BLE001 — surface any DB-side error generically
        db_ok = False
        db_detail = type(e).__name__

    upload_dir = settings().UPLOAD_DIR
    uploads_ok = os.path.isdir(upload_dir) and os.access(upload_dir, os.W_OK)

    if db_ok and uploads_ok:
        return {
            "data": {"status": "ok", "db": "ok", "uploads": "ok"},
            "status": {"category": "ok", "message": "OK"},
        }

    raise HTTPException(
        status_code=503,
        detail={
            "message": "service unhealthy",
            "db": "ok" if db_ok else f"error: {db_detail}",
            "uploads": "ok" if uploads_ok else f"not writable: {upload_dir}",
        },
    )
