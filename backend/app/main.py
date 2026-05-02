from __future__ import annotations

import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.core.logging import configure_logging

# Configure structured logging once at import time, before anything else
# tries to log. uvicorn replays logs through the root logger so this
# captures its lifecycle messages too.
configure_logging()


# Top-level (testable) Sentry event scrubber. Runs as `before_send` so the
# event is mutated before it leaves the worker. We have to scrub here
# rather than rely on Sentry's defaults because:
# - PATCH /api/workspaces/current carries the workspace's plaintext
#   provider API keys + scanner license key in the body. A 5xx during
#   that PATCH would otherwise ship those credentials to Sentry.
# - POST /api/workspaces/{id}/switch carries the target workspace_id —
#   tenant-identifying, low value to triage.
# - Cookie / Authorization / X-Workspace-Id headers are tenant- or
#   session-identifying. Sentry redacts `Cookie` by default, but we
#   strip explicitly so we don't depend on the SDK's redaction list.
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
    url = (request.get("url") or "").lower()
    method = (request.get("method") or "").upper()
    if method in ("PATCH", "POST") and "/api/workspaces" in url:
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
