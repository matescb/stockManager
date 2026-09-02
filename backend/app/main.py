from __future__ import annotations

import logging
import os
import re
import sys
from collections.abc import Sequence
from contextlib import asynccontextmanager
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

_SENTRY_SENSITIVE_TEXT_RE = re.compile(
    r"""(?ix)
    (?P<prefix>
        ["']?
        \b(?:password|passwd|pass|token|secret|api[_-]?key|authorization|cookie|session(?:[_-]?id)?)
        ["']?
        \s*[:=]\s*
        ["']?
    )
    (?P<value>[^"',&\s;}\]]+)
    """
)

# API-token plaintexts (`smk_{id_hex}.{secret}`, ADR-0029) carry no
# `token=` prefix when they land in an exception message or a breadcrumb —
# e.g. a client echoing back the header value, or a ValueError built from
# the raw string. The prefix rule above would miss those entirely, so the
# `smk_` shape is matched on its own. Deliberately anchored on the literal
# prefix, which is exactly why the plaintext carries one.
_SENTRY_API_TOKEN_RE = re.compile(r"\bsmk_[0-9a-fA-F]{32}\.[A-Za-z0-9_-]+")


def _strip_query_string(raw_url: str) -> str:
    fragment_index = raw_url.find("#")
    before_fragment = raw_url if fragment_index == -1 else raw_url[:fragment_index]
    fragment = "" if fragment_index == -1 else raw_url[fragment_index:]
    query_index = before_fragment.find("?")
    fragment_query_index = fragment.find("?")

    clean_before_fragment = (
        before_fragment if query_index == -1 else before_fragment[:query_index]
    )
    clean_fragment = (
        fragment if fragment_query_index == -1 else fragment[:fragment_query_index]
    )
    return f"{clean_before_fragment}{clean_fragment}"


def _scrub_sensitive_text(value: str) -> str:
    scrubbed = _SENTRY_SENSITIVE_TEXT_RE.sub(r"\g<prefix>[Filtered]", value)
    return _SENTRY_API_TOKEN_RE.sub("smk_[Filtered]", scrubbed)


def _scrub_event_strings(event: dict) -> None:
    if isinstance(event.get("message"), str):
        event["message"] = _scrub_sensitive_text(event["message"])

    exception = event.get("exception")
    if not isinstance(exception, dict):
        return
    values = exception.get("values")
    if not isinstance(values, list):
        return
    for item in values:
        if not isinstance(item, dict):
            continue
        for key in ("value", "message"):
            if isinstance(item.get(key), str):
                item[key] = _scrub_sensitive_text(item[key])


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
    if isinstance(event, dict):
        _scrub_event_strings(event)

    request = event.get("request")
    if not isinstance(request, dict):
        return event
    if isinstance(request.get("url"), str):
        request["url"] = _strip_query_string(request["url"])
    request.pop("query_string", None)
    headers = request.get("headers")
    if isinstance(headers, dict):
        drop = {"cookie", "authorization", "x-api-key", "x-workspace-id"}
        for k in list(headers.keys()):
            if k.lower() in drop:
                headers.pop(k, None)
            elif k.lower() in {"referer", "referrer"} and isinstance(
                headers.get(k), str
            ):
                headers[k] = _strip_query_string(headers[k])
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
        traces_sample_rate=cfg.SENTRY_TRACES_SAMPLE_RATE or 0.0,
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
    audit,
    auth,
    bom_presets,
    builds,
    catalog,
    categories,
    custom_fields,
    eda,
    eda_import,
    invitations,
    kicad,
    lots,
    orders,
    parts_assets,
    parts_core,
    parts_provider,
    parts_relations,
    parts_scan,
    projects,
    reports,
    search,
    sentry_tunnel,
    sourcing,
    stock,
    storage,
    tags,
    tokens,
    workspaces,
)
from app.core.config import settings
from app.core.deps import require_member_for_writes
from app.core.request_id import RequestIdMiddleware
from app.core.responses import http_exception_handler, validation_exception_handler

_is_prod = settings().APP_ENV == "prod"

_log = logging.getLogger(__name__)

CORS_ALLOW_HEADERS = [
    "Accept",
    "Accept-Language",
    "Baggage",
    "Content-Language",
    "Content-Type",
    "Sentry-Trace",
    "X-Request-Id",
    "X-Workspace-Id",
]


def _argv_option_value(argv: Sequence[str], option: str) -> str | None:
    """Return a uvicorn CLI option value from either --opt=value or --opt value."""
    for idx, arg in enumerate(argv):
        if arg == option:
            if idx + 1 < len(argv):
                return argv[idx + 1]
            return ""
        prefix = f"{option}="
        if arg.startswith(prefix):
            return arg[len(prefix):]
    return None


def assert_proxy_headers_trusted(argv: Sequence[str] | None = None) -> None:
    """Fail prod startup when uvicorn won't trust reverse-proxy client headers."""
    if settings().APP_ENV != "prod":
        return

    args = tuple(sys.argv if argv is None else argv)
    if "--proxy-headers" not in args or "--no-proxy-headers" in args:
        raise RuntimeError(
            "APP_ENV=prod requires uvicorn --proxy-headers so request.client.host "
            "is derived from Apache/nginx X-Forwarded-For."
        )
    if _argv_option_value(args, "--forwarded-allow-ips") != "*":
        raise RuntimeError(
            "APP_ENV=prod requires uvicorn --forwarded-allow-ips=* so proxy "
            "headers from the compose-network reverse proxy are trusted."
        )


def assert_cors_origins_not_wildcard() -> None:
    """Fail prod startup when credentialed CORS origins are unsafe or empty."""
    cfg = settings()
    if cfg.APP_ENV != "prod":
        return
    if not cfg.cors_origin_list:
        raise RuntimeError(
            "APP_ENV=prod requires at least one CORS_ORIGINS entry. Configure "
            "explicit frontend origins instead of starting with broken CORS."
        )
    if "*" in cfg.cors_origin_list:
        raise RuntimeError(
            "APP_ENV=prod rejects CORS_ORIGINS=* because wildcard origins cannot "
            "be used with credentialed CORS. Configure explicit frontend origins."
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Run synchronous startup assertions before serving requests."""
    assert_cors_origins_not_wildcard()
    assert_proxy_headers_trusted()
    try:
        yield
    finally:
        from app.domain.sourcing.providers.factory import close_provider_client_pool

        close_provider_client_pool()


app = FastAPI(
    title="Parts Inventory & Production Manager",
    version="0.1.0",
    # Disable OpenAPI surface in prod — the schema is a free attacker
    # roadmap to every endpoint, parameter shape, and response. Kept on
    # in dev where the interactive playground is useful.
    docs_url=None if _is_prod else "/docs",
    redoc_url=None if _is_prod else "/redoc",
    openapi_url=None if _is_prod else "/openapi.json",
    lifespan=lifespan,
)

# Rate limiter wired before CORS so 429 responses still get the right headers.
from slowapi.errors import RateLimitExceeded  # noqa: E402

from app.core.ratelimit import limiter  # noqa: E402

app.state.limiter = limiter


def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """Return a 429 in the standard {data, status} envelope (SEC2-017).

    slowapi's default handler returns ``{"error": "..."}`` which violates
    the API-envelope invariant. We replace it here and inject
    ``retry_after_seconds`` from the limit's own expiry window so the
    frontend can surface "try again in N seconds" without parsing the
    ``Retry-After`` header."""
    from fastapi.responses import JSONResponse

    from app.core.errors import ErrorCodes
    from app.core.responses import err

    # The limit object carries the window size in seconds via get_expiry().
    retry_after: int | None = None
    try:
        retry_after = int(exc.limit.limit.get_expiry())
    except Exception:
        pass

    body = err("rate_limited", f"rate limit exceeded: {exc.detail}")
    body["code"] = ErrorCodes.RATE_LIMITED
    if retry_after is not None:
        body["retry_after_seconds"] = retry_after

    response = JSONResponse(content=body, status_code=429)
    # Let slowapi inject its standard Retry-After / X-RateLimit-* headers.
    response = request.app.state.limiter._inject_headers(
        response, request.state.view_rate_limit
    )
    return response


app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

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
# - /api/sentry-tunnel: the route enforces its own session-or-trusted-Origin
#   gate so pre-auth same-origin SDK events can still be reported.
# - /api/auth/login + /signup: not yet authenticated; the threat
#   here is brute force, handled by slowapi rate limiting.
_CSRF_EXEMPT_PATHS = frozenset({
    "/api/sentry-tunnel",
    "/api/auth/login",
    "/api/auth/signup",
    # /auth/verify is a pre-auth endpoint: the user follows a link from
    # their email client, which carries no Origin header (or a mail-client
    # origin). Exempt so the CSRF middleware doesn't block the verify call.
    "/api/auth/verify",
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
        # API-token requests are exempt (ADR-0029). Two facts make this
        # safe, and BOTH have to hold:
        #  (a) A browser cannot attach an `Authorization` header to a
        #      cross-site request without a CORS preflight, and
        #      `CORS_ALLOW_HEADERS` (above) deliberately omits
        #      `Authorization` — so the preflight fails even from an
        #      allow-listed origin. Form posts, <img>, and friends — the
        #      shapes CSRF actually takes — cannot set headers at all.
        #      Do not add "Authorization" to CORS_ALLOW_HEADERS.
        #  (b) `core/deps.py::get_current_user` treats ANY non-empty
        #      Authorization header as a commitment to the token path
        #      with no cookie fallback. So a forged header doesn't ride
        #      the victim's session — it just needs a valid token, and
        #      an attacker who has one doesn't need CSRF.
        # The truthiness test here must stay identical to the one in
        # deps.py: if the two disagreed about what counts as "present",
        # a value that skips CSRF here but falls back to the cookie
        # there would be a real forgery hole. Pinned by
        # tests/test_api_tokens.py's CSRF matrix.
        #
        # ...but leg (b) only holds for routes that authenticate through
        # `get_current_user`. `/api/auth/logout` reads the session cookie
        # DIRECTLY, so an Authorization header does not disable cookie
        # auth there and the exemption would strip its only defence. The
        # skip is therefore never applied under /api/auth/ — no API-token
        # client has any business calling logout, password-reset-request
        # or reset-password, so nothing legitimate is lost. (The pre-auth
        # auth routes that DO need an exemption are in _CSRF_EXEMPT_PATHS
        # above, checked before we get here.)
        if request.headers.get("Authorization") and not request.url.path.startswith(
            "/api/auth/"
        ):
            return await call_next(request)
        origin = _origin_host(request.headers.get("origin")) or _origin_host(
            request.headers.get("referer")
        )
        if origin is None or origin not in self._allowed:
            # RequestIdMiddleware runs outside us (added last → outermost),
            # so request.state.request_id is set by the time we get here.
            # Surface it in the body and as a response header so the rejected
            # call is still correlatable in logs/Sentry.
            rid = getattr(request.state, "request_id", None)
            content: dict[str, object] = {
                "data": None,
                "status": {
                    "category": "forbidden",
                    "message": "cross-origin request blocked",
                },
            }
            headers: dict[str, str] = {}
            if rid:
                content["request_id"] = rid
                headers["X-Request-Id"] = rid
            return JSONResponse(
                status_code=403,
                content=content,
                headers=headers or None,
            )
        return await call_next(request)


# Starlette stacks middleware LIFO: the LAST add_middleware() call becomes
# the OUTERMOST wrapper. RequestIdMiddleware must wrap CORS / CSRF so that
# request.state.request_id is set even when those middlewares short-circuit
# the request (CSRF 403, CORS preflight). BE2-012 / issue #61.
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
    allow_headers=CORS_ALLOW_HEADERS,
)

# Added last → outermost. Sees every request before CORS/CSRF run.
app.add_middleware(RequestIdMiddleware)

app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)

os.makedirs(settings().UPLOAD_DIR, exist_ok=True)

_member_gate = [Depends(require_member_for_writes)]

app.include_router(audit.router, prefix="/api/audit", tags=["audit"], dependencies=_member_gate)
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(workspaces.router, prefix="/api/workspaces", tags=["workspaces"])
app.include_router(parts_core.router, prefix="/api/parts", tags=["parts"], dependencies=_member_gate)
app.include_router(parts_assets.router, prefix="/api/parts", tags=["parts"], dependencies=_member_gate)
app.include_router(
    parts_relations.router,
    prefix="/api/parts",
    tags=["parts"],
    dependencies=_member_gate,
)
app.include_router(parts_scan.router, prefix="/api/parts", tags=["parts"], dependencies=_member_gate)
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
# on workspace membership — the SDK fires from the login screen too — but
# the route enforces its own session-or-trusted-Origin gate.
app.include_router(sentry_tunnel.router, prefix="/api", tags=["sentry"])
app.include_router(attachments.router, prefix="/api/attachments", tags=["attachments"], dependencies=_member_gate)
app.include_router(
    categories.router,
    prefix="/api/categories",
    tags=["categories"],
    dependencies=_member_gate,
)
app.include_router(
    eda.router,
    prefix="/api/eda",
    tags=["eda"],
    dependencies=_member_gate,
)
app.include_router(
    eda_import.router,
    prefix="/api/eda",
    tags=["eda"],
    dependencies=_member_gate,
)
# Second mount: the per-part EDA config lives under the part it belongs
# to. Same multi-mount shape as `sourcing.parts_router` below.
app.include_router(
    eda.parts_router,
    prefix="/api/parts",
    tags=["eda"],
    dependencies=_member_gate,
)
app.include_router(
    eda_import.parts_router,
    prefix="/api/parts",
    tags=["eda"],
    dependencies=_member_gate,
)
app.include_router(custom_fields.router, prefix="/api/custom-fields", tags=["custom_fields"], dependencies=_member_gate)
app.include_router(tags.router, prefix="/api/tags", tags=["tags"], dependencies=_member_gate)
# No `_member_gate`: a viewer may mint their own token, because the token
# inherits the viewer role and so can't do anything the viewer couldn't.
# See the module docstring in routes/tokens.py.
app.include_router(tokens.router, prefix="/api/tokens", tags=["tokens"])
app.include_router(search.router, prefix="/api/search", tags=["search"], dependencies=_member_gate)
app.include_router(sourcing.router, prefix="/api/workspaces", tags=["sourcing"])
app.include_router(sourcing.search_router, prefix="/api/sourcing", tags=["sourcing"])
app.include_router(
    sourcing.projects_router,
    prefix="/api/projects",
    tags=["sourcing"],
    dependencies=_member_gate,
)
app.include_router(
    sourcing.parts_router,
    prefix="/api/parts",
    tags=["sourcing"],
    dependencies=_member_gate,
)
app.include_router(
    parts_provider.router,
    prefix="/api/parts",
    tags=["parts_provider"],
    dependencies=_member_gate,
)

# Public, token-gated read-only catalog. Mounted AFTER the /api routers and
# intentionally without a member-gate dependency.
app.include_router(catalog.router, prefix="/catalog", tags=["catalog"])

# KiCad HTTP library. Outside /api because KiCad parses fixed raw-JSON
# documents rather than the `{data, status}` envelope, and without
# `_member_gate` because it carries its own always-404 PAT dependency
# (`routes/kicad.py::kicad_workspace`) instead of the session cookie.
app.include_router(kicad.router, prefix=kicad.API_PREFIX, tags=["kicad"])


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
