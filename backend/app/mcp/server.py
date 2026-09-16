"""The MCP server, its transport, and how it is mounted at `/mcp`.

Assembles three pieces: the SDK's `MCPServer` with every tool in
`app/mcp/tools` registered on it, the streamable-HTTP transport in
stateless mode, and the ASGI wrapper that authenticates each request.
`app/main.py` uses `mount_mcp(app)` and `lifespan_context()`; nothing
else here is public.

**Stateless, and why it is not just a default.** Stateless mode handles
each request inline in the caller's task, with no session to persist
between them. That buys three things at once: it fits `--workers 1` and
would keep fitting if that ever changed, since there is no session state
for a second worker to miss; there is nothing to expire, evict, or leak;
and — the load-bearing one — the contextvar carrying the authenticated
principal reaches the tool, because the tool runs inside the request's
own context rather than in a task adopted by a task group started at
lifespan time. See `app/mcp/principal.py`.

**Mounted as middleware, not as a route.** Starlette's `Mount("/mcp",
…)` does not match `/mcp` itself — only `/mcp/…` — so the bare path
falls through to `redirect_slashes` and answers 307. MCP clients POST
their JSON-RPC body and do not follow that redirect, so the documented
URL would simply not work. A pure-ASGI dispatcher in the middleware
stack sidesteps the router entirely, answers both `/mcp` and `/mcp/`,
and has the useful side effect of putting the surface outside CORS and
the CSRF Origin guard — which is correct here and not a shortcut: this
surface refuses cookie authentication outright (`app/mcp/auth.py`), and
CSRF is a cookie attack.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.mcp.auth import McpAuthMiddleware
from app.mcp.tools import load_tools
from app.mcp.tools._shared import MAX_DECODED_BYTES

_log = logging.getLogger(__name__)

# The path the server answers on, and the path `deploy/nginx-web.conf`
# must route. `tests/test_deploy_nginx_routes.py` reads this constant so
# the two cannot drift.
MCP_PATH = "/mcp"

# Room for the largest accepted base64 payload plus JSON-RPC framing.
# `MAX_DECODED_BYTES` is the real limit and is enforced per argument in
# `tools/_shared.decode_base64`; this is the transport's coarse guard,
# sized so it never fires first and turns a clean tool error into an
# unexplained connection failure. base64 costs 4/3, hence the headroom.
_MAX_BODY_BYTES = MAX_DECODED_BYTES * 2

# Advertised to clients in the initialize handshake. Tracks the
# `FastAPI(version=…)` in `main.py`: this is one product, and two
# version numbers for it would only ever confuse a bug report.
SERVER_VERSION = "0.1.0"

# Sent to the model once per session, before it has called anything. It
# carries what a tool docstring cannot: the ORDER calls go in, and the
# handful of rules that are invisible until a call is refused (the
# workspace pin, the duplicate-MPN success, mandatory storage, the
# read-before-replace on set_part_eda). Kept to the shape of a briefing
# rather than a manual — every line here is paid for on every session,
# and the per-tool detail is already in the docstrings the client
# receives alongside it. `docs/api/mcp.md` is the long form;
# `tests/test_mcp.py::test_instructions_name_every_write_tool` pins the
# tool names so a renamed or added write tool cannot silently drop out.
INSTRUCTIONS = (
    "Tools for an electronics parts inventory: parts and their "
    "specifications, stock by storage location, KiCad CAD data "
    "(symbols, footprints, 3D models, SPICE), and project BOMs.\n"
    "\n"
    "SCOPE. Every tool acts on the one workspace the access token "
    "belongs to. Nothing switches workspaces, and an id belonging to "
    "another one reads as not-found rather than forbidden. Ids are "
    "opaque strings. Most part arguments also accept an exact "
    "manufacturer part number; the argument is spelled id_or_mpn, "
    "part_id or part_id_or_mpn depending on the tool.\n"
    "\n"
    "ADDING A PART: create_part, then set_part_category, then "
    "set_part_specs. create_part needs at least one of name and mpn, "
    "and name defaults to the mpn. There is no provider lookup on this "
    "surface — the mpn, manufacturer and specifications come from you. "
    "create_part on an mpn this workspace already holds SUCCEEDS, "
    "writes nothing, and returns that part with found_existing true; "
    "check that flag before reporting that you added anything. Call "
    "list_categories for a category, and create_category if the one "
    "you want is missing. create_part never sets stock.\n"
    "\n"
    "ADDING STOCK: find or create the part, call "
    "list_storage_locations, then add_stock with a location id. A part "
    "configured with a mandatory default location refuses any other "
    "location, and refuses a call that names none. consume_stock "
    "without a location takes from the unassigned pool only, NOT from "
    "wherever the stock happens to sit — read get_part first. "
    "move_stock needs both from_location_id and to_location_id.\n"
    "\n"
    "CAD DATA: find_parts_missing_eda finds the work. Call get_part_eda "
    "BEFORE set_part_eda: set_part_eda replaces the whole "
    "configuration and writes every omitted argument as its default, so "
    "pass back what you want to keep. import_vendor_zip, fetch_lcsc and "
    "upload_eda_asset bring files in and fill a part's empty slots "
    "themselves, which often makes set_part_eda unnecessary.\n"
    "\n"
    "BUILDS: list_projects, then bom_shortages for what is short, "
    "get_project_bom for the lines themselves, stock_levels for the "
    "on-hand / reserved / available split, and sourcing_offers for "
    "distributor price. sourcing_offers spends metered quota — call it "
    "for the shortages, not for every BOM line.\n"
    "\n"
    "set_part_specs writes your own values and never overwrites ones a "
    "parts provider supplied; those come back under "
    "skipped_provider_owned. Reserved keys (image_url, datasheet_url, "
    "source_url, and anything prefixed digikey: or mouser:) refuse the "
    "whole call.\n"
    "\n"
    "Every refusal begins with a stable error code. A read-only token "
    "lists the write tools and is refused only when it calls one, with "
    "auth.token_read_only."
)


def _build_server() -> MCPServer:
    server = MCPServer(
        name="stockmanager",
        title="Parts Inventory & Production Manager",
        version=SERVER_VERSION,
        instructions=INSTRUCTIONS,
    )
    for spec in load_tools():
        server.add_tool(spec.fn, name=spec.name)
    return server


_server = _build_server()

# Set by `lifespan_context` and read by the dispatcher on every request.
# Module-level mutable state, which needs the justification below.
_authenticated_app: ASGIApp | None = None


def _build_transport_app():
    """A transport app with a FRESH streamable-HTTP session manager.

    Called once per lifespan rather than once at import, because the
    SDK's `StreamableHTTPSessionManager.run()` is single-use — a second
    call on the same instance raises "can only be called once per
    instance". In production that distinction never shows up: one
    process, one lifespan, one manager. It shows up in the test suite,
    which starts a lifespan per test, and a manager built at import
    would make the second test in every file fail on a detail of the
    SDK's internals rather than on anything about this app.
    """
    return _server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        max_request_body_size=_MAX_BODY_BYTES,
        # DNS-rebinding protection off, deliberately. It exists to stop
        # a browser on the same machine reaching an MCP server bound to
        # localhost, by pinning the Host header to a list of local
        # names. This server is not bound to localhost — it is behind
        # nginx on a deployment hostname, so the allow-list would have
        # to be kept in step with that hostname or the whole surface
        # 421s. What it would be defending against is already gone: a
        # browser cannot attach the `Authorization` header this surface
        # requires to a cross-origin request without a CORS preflight,
        # and `main.py`'s `CORS_ALLOW_HEADERS` deliberately omits
        # `Authorization`. Same argument ADR-0029 makes for the CSRF
        # exemption.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )


class McpDispatchMiddleware:
    """Route `/mcp` and `/mcp/` to the MCP app; pass everything else on.

    Pure ASGI rather than `BaseHTTPMiddleware`, which buffers responses
    and would break the transport's streamed `text/event-stream` body.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path", "").rstrip("/") != MCP_PATH:
            await self.app(scope, receive, send)
            return
        if _authenticated_app is None:
            # Only reachable if a request arrives before startup
            # finished or after shutdown began. 503 rather than a crash,
            # so a health probe racing the boot reads as "not ready yet".
            await _not_ready(send)
            return
        # Normalised so the transport's own route matches whichever form
        # the client used. A shallow copy, because the scope is the
        # outer app's and the trailing-slash rewrite must not leak back
        # into its logging or Sentry's transaction name.
        await _authenticated_app({**scope, "path": MCP_PATH}, receive, send)


async def _not_ready(send: Send) -> None:
    body = b'{"jsonrpc":"2.0","id":null,"error":{"code":-32002,"message":"mcp server not ready"}}'
    await send(
        {
            "type": "http.response.start",
            "status": 503,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


@asynccontextmanager
async def lifespan_context(app) -> AsyncIterator[None]:
    """Run the transport's session manager for the life of the process.

    Mounted sub-applications do not get their lifespan run by the parent
    — Starlette propagates lifespan to the app it is serving and no
    further. Without this the session manager's task group is never
    started and the first request fails with "Task group is not
    initialized", at runtime, in prod. `app/main.py` enters this from
    its own lifespan.
    """
    global _authenticated_app

    if not settings().MCP_ENABLED:
        yield
        return
    transport_app = _build_transport_app()
    _authenticated_app = McpAuthMiddleware(transport_app)
    try:
        async with transport_app.router.lifespan_context(app):
            _log.info(
                "mcp server mounted at %s (%d tools)", MCP_PATH, len(load_tools())
            )
            yield
    finally:
        # Cleared on the way out so a request arriving during shutdown
        # gets the 503 above rather than reaching a session manager
        # whose task group has already been torn down.
        _authenticated_app = None


def mount_mcp(app) -> None:
    """Install the dispatcher, unless `MCP_ENABLED` is false.

    Not installing it is what makes the kill switch total: with the
    middleware absent, `/mcp` is an unrouted path and the app 404s it
    exactly as it would any other, with no hint that a server was ever
    there.
    """
    if not settings().MCP_ENABLED:
        _log.info("mcp server disabled (MCP_ENABLED=false)")
        return
    app.add_middleware(McpDispatchMiddleware)
