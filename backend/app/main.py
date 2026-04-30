from __future__ import annotations

import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

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
        traces_sample_rate=cfg.SENTRY_TRACES_SAMPLE_RATE,
        # Per the Sentry FastAPI wizard. Sends request headers and the
        # user IP. Cookies are redacted automatically; if a particular
        # header (e.g. Authorization) needs scrubbing, add a
        # before_send hook here. Flip to False if you want strict
        # data-minimisation.
        send_default_pii=True,
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

app = FastAPI(title="Parts Inventory & Production Manager", version="0.1.0")

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
    return {"data": {"status": "ok"}, "status": {"category": "ok", "message": "OK"}}


# Sentry verification endpoint per the FastAPI wizard. Only mounted when a
# DSN is actually configured — keeps it absent from dev / from forks that
# disable error tracking. Hit it once after wiring the DSN, confirm the
# event appears in Sentry, then remove this block.
if settings().SENTRY_DSN:
    @app.get("/api/sentry-debug")
    def trigger_error():  # pragma: no cover
        division_by_zero = 1 / 0  # noqa: F841 — intentional ZeroDivisionError
        return {"unreachable": True}
