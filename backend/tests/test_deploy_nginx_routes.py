"""Every backend surface mounted outside `/api` is routed by prod nginx.

`deploy/nginx-web.conf` ends in an SPA fallback (`try_files … /index.html`),
so a path nginx doesn't route explicitly is answered by the **web**
container with `200 text/html` — it never reaches uvicorn. The host-side
Apache proxies `/` wholesale to that container, so there is no second
chance upstream.

A 200 with the wrong body is the worst failure mode available here, and
it is invisible to every other gate we have:

* KiCad's HTTP library checks the status code and nothing else. It
  accepts the SPA index page and then throws inside its JSON parse; the
  user sees a dead library and no usable error.
* A public catalog link handed to a customer renders the app shell,
  which bounces them to a login they aren't supposed to need.

`/api/*` has been routed since the beginning, so this only bites routers
mounted elsewhere — `/catalog` (which shipped with the gap) and
`/kicad-api`. This test enumerates the app's own routes rather than
checking a hard-coded list, so the next such router fails the build
instead of shipping.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from app.main import app

_NGINX_CONF = Path(__file__).resolve().parents[2] / "deploy" / "nginx-web.conf"

# FastAPI's own docs routes. They are not part of the product's HTTP
# surface, are not linked from anywhere, and are deliberately left to the
# SPA fallback rather than exposed through the prod proxy.
_UNROUTED_PREFIXES = frozenset({"docs", "openapi.json", "redoc"})


def _first_segment(path: str) -> str:
    return path.lstrip("/").split("/", 1)[0]


def _non_api_prefixes() -> set[str]:
    prefixes = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        segment = _first_segment(route.path)
        if segment in ("api", *_UNROUTED_PREFIXES):
            continue
        prefixes.add(segment)
    prefixes |= _middleware_mounted_prefixes()
    return prefixes


def _middleware_mounted_prefixes() -> set[str]:
    """Backend surfaces served from the middleware stack, not the router.

    `app.routes` is not the whole HTTP surface. The MCP server
    (ADR-0030) is dispatched by a pure-ASGI middleware rather than a
    route, because Starlette's `Mount` cannot answer the bare `/mcp`
    path — so it is invisible to the enumeration above while being
    exactly the kind of non-`/api` surface this test exists to protect.
    Read off the module constant rather than hard-coded, so renaming the
    path moves the assertion with it.
    """
    from app.mcp.server import MCP_PATH

    return {_first_segment(MCP_PATH)}


def _proxied_locations(conf: str) -> set[str]:
    """First path segment of every `location` block that proxies upstream.

    Deliberately naive: it pairs each `location <match>` with the body
    that follows it and keeps the ones containing `proxy_pass`. That is
    enough to answer "does traffic under this prefix reach the backend",
    which is the only question here.
    """
    found = set()
    # The optional group is nginx's location modifier (`=` exact, `~` /
    # `~*` regex, `^~` prefix-no-regex), which is a separate token from
    # the path. Without consuming it, `location = /mcp {` matched with
    # `=` as the path and the real prefix went unrecorded.
    for match in re.finditer(r"location\s+(?:(=|\^~|~\*?)\s+)?([^\s{]+)\s*\{", conf):
        body_start = match.end()
        body = conf[body_start : conf.find("}", body_start)]
        if "proxy_pass" not in body:
            continue
        # Strip nginx's regex/prefix modifiers and any leading anchor.
        target = match.group(2).lstrip("^~").lstrip("/")
        found.add(target.split("/", 1)[0])
    return found


def test_every_non_api_router_is_proxied_by_nginx():
    conf = _NGINX_CONF.read_text()
    proxied = _proxied_locations(conf)
    missing = sorted(p for p in _non_api_prefixes() if p not in proxied)
    assert not missing, (
        f"mounted outside /api but not routed in {_NGINX_CONF.name}: {missing}. "
        "In prod these fall into the SPA fallback and return 200 text/html "
        "instead of reaching the backend — add a `location /<prefix>/` block "
        "mirroring the /api/ one."
    )


@pytest.mark.parametrize("prefix", ["/api/", "/kicad-api/", "/catalog/", "/mcp/"])
def test_proxy_blocks_carry_the_forwarding_headers(prefix: str):
    """A proxied block without `X-Forwarded-For` makes every client look
    like the docker bridge IP, which collapses slowapi's per-IP buckets
    into one (the same class of bug as the compose `command:` regression
    in CLAUDE.md)."""
    conf = _NGINX_CONF.read_text()
    match = re.search(rf"location {re.escape(prefix)}\s*\{{", conf)
    assert match, f"no `location {prefix}` block in {_NGINX_CONF.name}"
    body = conf[match.end() : conf.find("}", match.end())]
    for directive in (
        "proxy_pass http://backend:8000",
        "X-Real-IP",
        "X-Forwarded-For",
        "X-Forwarded-Proto",
        # SEC2-018: don't advertise "Server: uvicorn".
        "proxy_hide_header Server",
    ):
        assert directive in body, f"{prefix} block is missing `{directive}`"


def _csp() -> dict[str, str]:
    """The prod CSP, parsed into `directive -> value`."""
    conf = _NGINX_CONF.read_text()
    match = re.search(r'add_header Content-Security-Policy "([^"]+)"', conf)
    assert match, f"no Content-Security-Policy header in {_NGINX_CONF.name}"
    directives = {}
    for part in match.group(1).split(";"):
        name, _, value = part.strip().partition(" ")
        if name:
            directives[name] = value
    return directives


def test_csp_locks_executable_directives_to_self():
    """The CSP keeps the executable directives locked down.

    `default-src` stays `'self'`, and the permissive schemes the image
    directive allows (`data:`) must never leak into `script-src`,
    `style-src` or `connect-src` — a `data:`/`blob:` script source is most
    of the way to defeating `script-src 'self'`. (The 2D CAD previews are
    server-rendered SVGs shown via `<img>`, so `img-src 'self' data:` is
    all they need — no `blob:` — see ADR-0032.)
    """
    csp = _csp()
    assert csp["default-src"] == "'self'"
    for directive in ("script-src", "style-src", "connect-src", "default-src"):
        for scheme in ("blob:", "data:"):
            assert scheme not in csp.get(directive, ""), (
                f"{scheme} leaked into {directive}; it belongs in img-src only"
            )
