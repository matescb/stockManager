"""The MCP surface at `/mcp` — auth, the write gate, tools, isolation.

Driven through the SDK's own client over an in-process ASGI transport,
not by hand-rolling JSON-RPC. That matters: the thing being tested is
whether a real MCP client can connect and call tools, and a hand-built
request would pass while, say, the `Accept` negotiation or the
`text/event-stream` framing was broken for every actual client.

Three properties are load-bearing enough to be pinned rather than
trusted, and each is pinned BEHAVIOURALLY — by making the system do the
thing and observing the result — because the structural versions of all
three have already been shown to pass while the property was false:

* that a `writes=False` tool really does not write. Checked by running
  every read tool against a seeded workspace and letting the database
  object (`test_read_tools_touch_nothing`), plus a deliberately
  misdeclared tool to prove the guard fires. The structural list test
  that used to stand for this compared one declaration against another
  and passed throughout the `sourcing_offers` bug.
* that the principal contextvar reaches the tool and does not cross
  tenants, with the overlap FORCED by a barrier rather than merely
  started concurrently.
* that every tool has a rate ceiling and that ceilings do not share a
  bucket, checked with the limiter force-enabled — it is disabled
  outside prod, so nothing else here exercises it.
"""
from __future__ import annotations

import base64
import io
import json
import uuid
import zipfile
from contextlib import asynccontextmanager, contextmanager
from datetime import timedelta

import anyio
import httpx2
import pytest
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from app.domain.audit.models import AuditLog
from app.domain.custom_fields.models import CustomField
from app.domain.eda.models import EdaSymbol
from app.domain.parts.models import Part
from app.domain.stock.models import StockEntry
from app.main import app
from app.mcp import server as mcp_server
from app.mcp.tools import load_tools
from tests._factories import create_part, create_storage, signup_user
from tests.test_eda import STEP_BYTES, _footprint_text, _symbol_text

MCP_URL = "http://testserver/mcp"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _mint(client: TestClient, **body) -> dict:
    body.setdefault("label", "mcp ci token")
    r = client.post("/api/tokens", json=body)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _join_workspace(host: TestClient, role: str) -> TestClient:
    """A second user in the host's workspace, at `role`.

    The switch at the end is not incidental. Signing up creates the
    joiner their OWN workspace, and a token is pinned to whichever
    workspace was active when it was minted — so without switching,
    `_mint(joiner)` would produce a credential for the joiner's private
    workspace and the role tests would be asserting nothing.
    """
    email = f"{role}-{uuid.uuid4().hex[:8]}@x.com"
    invite = host.post("/api/invitations", json={"email": email, "role": role})
    assert invite.status_code in (200, 201), invite.text
    joiner = TestClient(app)
    signup_user(joiner, email=email)
    accepted = joiner.post(
        "/api/invitations/accept", json={"token": invite.json()["data"]["token"]}
    )
    assert accepted.status_code == 200, accepted.text

    host_ws = host.get("/api/auth/me").json()["data"]["workspaces"][0]["id"]
    switched = joiner.post(f"/api/workspaces/{host_ws}/switch")
    assert switched.status_code == 200, switched.text
    return joiner


@asynccontextmanager
async def mcp_server_running():
    """Start the MCP server's session manager, as app startup does.

    Enters the MCP server's lifespan rather than the whole app's: the
    streamable-HTTP session manager's task group has to be running or
    every request fails with "Task group is not initialized", and that
    is the only part of app startup this surface needs.

    Exactly one of these may be open at a time, matching production
    where the lifespan is entered once per process. A test that wants
    two concurrent clients opens this once and `mcp_client` twice.
    """
    async with mcp_server.lifespan_context(app):
        yield


@asynccontextmanager
async def mcp_client(token: str | None = None, *, headers: dict | None = None):
    """An initialised client session against an already-running server.

    `terminate_on_close=False` because the transport is stateless —
    there is no session for a closing DELETE to terminate, and sending
    one only produces noise in the log.
    """
    sent = dict(headers or {})
    if token is not None:
        sent["Authorization"] = f"Token {token}"
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://testserver", headers=sent
    ) as http_client:
        async with streamable_http_client(
            MCP_URL, http_client=http_client, terminate_on_close=False
        ) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                yield session


@asynccontextmanager
async def mcp_session(token: str | None = None, *, headers: dict | None = None):
    """Server plus one client — what almost every test below wants."""
    async with mcp_server_running():
        async with mcp_client(token, headers=headers) as session:
            yield session


async def call(session: ClientSession, tool: str, /, **arguments):
    """Call a tool and return its decoded JSON result.

    `session` and `tool` are positional-only so that a tool argument
    literally called `name` (which `create_category` has) lands in
    `**arguments` instead of colliding with this function's own
    parameter.

    Tool results arrive as a text block holding the JSON document the
    tool returned; `structured_content` is only populated for tools with
    a declared output schema, which these deliberately do not have (a
    free-form dict is the right shape for a model to read).
    """
    result = await session.call_tool(tool, arguments)
    assert not result.is_error, _text(result)
    return json.loads(_text(result))


async def call_error(session: ClientSession, tool: str, /, **arguments) -> str:
    """Call a tool expecting a refusal; return the error text."""
    result = await session.call_tool(tool, arguments)
    assert result.is_error, f"expected {tool} to fail, got {_text(result)}"
    return _text(result)


def _text(result) -> str:
    return "\n".join(
        block.text for block in result.content if getattr(block, "text", None)
    )


def _leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for sub in exc.exceptions for leaf in _leaves(sub)]
    return [exc]


@asynccontextmanager
async def refuses_the_credential():
    """Assert the block fails authentication, whatever it is wrapped in.

    A rejected handshake surfaces as `MCPError`, but the SDK's client
    runs its transport in nested anyio task groups, so what actually
    reaches the test is an `ExceptionGroup` two or three levels deep
    with the `MCPError` at a leaf. `pytest.raises(MCPError)` does not
    match that. Unwrapping here keeps the six auth tests reading as
    assertions about authentication rather than about anyio.
    """
    try:
        yield
    except BaseException as exc:  # noqa: BLE001 — re-asserted, then suppressed
        errors = [e for e in _leaves(exc) if isinstance(e, MCPError)]
        assert errors, f"expected an MCP error, got {_leaves(exc)!r}"
        for error in errors:
            assert "invalid api token" in str(error), error
        return
    raise AssertionError("expected the credential to be refused")


def _zip_bytes(members: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(
                name, content if isinstance(content, bytes) else content.encode()
            )
    return buf.getvalue()


def _snapeda_zip(part: str = "MCPPART") -> bytes:
    return _zip_bytes(
        {
            f"{part}.kicad_sym": _symbol_text(part),
            f"{part}.kicad_mod": _footprint_text(f"{part}_FP"),
            f"{part}.step": STEP_BYTES,
        }
    )


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


@pytest.fixture
def full_token(authed_client) -> str:
    return _mint(authed_client)["token"]


@pytest.fixture
def readonly_token(authed_client) -> str:
    return _mint(authed_client, read_only=True)["token"]


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


async def test_no_credential_is_refused(db):
    async with refuses_the_credential():
        async with mcp_session():
            pass


async def test_unknown_token_is_refused(db):
    async with refuses_the_credential():
        async with mcp_session("smk_" + "0" * 32 + ".nope"):
            pass


async def test_malformed_token_is_refused(db):
    async with refuses_the_credential():
        async with mcp_session("not-even-close"):
            pass


async def test_revoked_token_is_refused(authed_client, full_token):
    token_id = authed_client.get("/api/tokens").json()["data"][0]["id"]
    assert authed_client.post(f"/api/tokens/{token_id}/revoke").status_code == 200
    async with refuses_the_credential():
        async with mcp_session(full_token):
            pass


async def test_expired_token_is_refused(authed_client, full_token, db):
    """Expiry is enforced on this surface too, not only over HTTP.

    Backdated in the database rather than waited for. The token was
    minted without an expiry, so this also covers the case of one being
    set after the fact.
    """
    from app.core.time import utcnow
    from app.domain.tokens.models import ApiToken

    token_id = uuid.UUID(authed_client.get("/api/tokens").json()["data"][0]["id"])
    row = db.get(ApiToken, token_id)
    row.expires_at = utcnow() - timedelta(minutes=1)
    db.flush()

    async with refuses_the_credential():
        async with mcp_session(full_token):
            pass


async def test_token_of_a_removed_member_is_refused(authed_client, db):
    """Losing the membership kills the token, mid-life.

    `resolve_live_token` re-checks the workspace membership on EVERY
    request, which is the only thing standing between a departed
    teammate's live token and their old workspace — the token row itself
    is untouched here. Asserted through `/mcp` because this surface
    reaches that check by a different path from the HTTP routes, and a
    refactor could plausibly bypass it for one and not the other.
    """
    from app.domain.workspaces.models import WorkspaceMember

    member = _join_workspace(authed_client, "member")
    token = _mint(member)["token"]

    # Works while the membership stands.
    async with mcp_session(token) as s:
        await call(s, "list_categories")

    member_user_id = uuid.UUID(member.get("/api/auth/me").json()["data"]["user"]["id"])
    host_ws = uuid.UUID(
        authed_client.get("/api/auth/me").json()["data"]["workspaces"][0]["id"]
    )
    row = (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.user_id == member_user_id,
            WorkspaceMember.workspace_id == host_ws,
        )
        .one()
    )
    row.status = "removed"
    db.flush()

    async with refuses_the_credential():
        async with mcp_session(token):
            pass


async def test_session_cookie_alone_is_refused(authed_client):
    """A logged-in browser session is not a credential for this surface.

    The point of the assertion: `/mcp` sits outside the CSRF Origin
    guard, and that is only sound while cookie auth is impossible here.
    If this ever passes, the middleware ordering in `main.py` has
    become a cross-site request forgery hole.
    """
    cookies = "; ".join(f"{k}={v}" for k, v in authed_client.cookies.items())
    assert cookies, "expected the authed client to hold a session cookie"
    async with refuses_the_credential():
        async with mcp_session(headers={"Cookie": cookies}):
            pass


async def test_unknown_auth_scheme_is_refused(db, authed_client):
    token = _mint(authed_client)["token"]
    async with refuses_the_credential():
        async with mcp_session(headers={"Authorization": f"Weird {token}"}):
            pass


async def test_bearer_scheme_is_accepted(authed_client, full_token):
    """Both schemes work, matching `core/deps.py::_authenticate_api_token`."""
    async with mcp_session(headers={"Authorization": f"Bearer {full_token}"}) as s:
        assert (await call(s, "list_categories"))["categories"] == []


async def test_token_from_another_workspace_sees_none_of_this_one(db):
    """Two-token isolation probe: A creates, B cannot see it."""
    a, b = TestClient(app), TestClient(app)
    signup_user(a)
    signup_user(b)
    part_id = create_part(a, "MCP-ISO widget", mpn="MCP-ISO-1")
    token_b = _mint(b)["token"]

    async with mcp_session(token_b) as s:
        assert (await call(s, "search_parts", query="MCP-ISO"))["parts"] == []
        assert "part.not_found" in await call_error(s, "get_part", id_or_mpn=part_id)
        assert "part.not_found" in await call_error(
            s, "get_part", id_or_mpn="MCP-ISO-1"
        )


def test_disabled_flag_takes_the_endpoint_off_the_network(db, monkeypatch):
    """`MCP_ENABLED=false` must 404, not 401 — the kill switch is total.

    A 401 would tell a caller the server is there and their credential
    is wrong; the point of the flag is that the surface is gone.

    Asserted against a throwaway app rather than the real one, because
    the dispatcher is installed at import time and flipping the flag
    cannot un-mount it from a process that has already started. What is
    under test is `mount_mcp`'s decision, which is the thing the flag
    actually controls.
    """
    from fastapi import FastAPI

    from app.core import config

    monkeypatch.setattr(config.settings(), "MCP_ENABLED", False)

    probe = FastAPI()

    @probe.get("/ping")
    def ping():
        return {"ok": True}

    mcp_server.mount_mcp(probe)

    client = TestClient(probe)
    assert client.get("/ping").status_code == 200
    for path in ("/mcp", "/mcp/"):
        response = client.post(path, json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert response.status_code == 404, (path, response.status_code)


async def test_disabled_flag_starts_no_session_manager(db, monkeypatch):
    """The other half of the flag: the lifespan becomes a no-op.

    Entered twice on purpose. A session manager may only be run once per
    instance, so a second entry that started one would raise — which is
    exactly how this asserts that neither entry started anything.
    """
    from app.core import config

    monkeypatch.setattr(config.settings(), "MCP_ENABLED", False)

    for _ in range(2):
        async with mcp_server.lifespan_context(app):
            pass


# ---------------------------------------------------------------------------
# Tool listing and the write set
# ---------------------------------------------------------------------------

_EXPECTED_WRITE_TOOLS = {
    "sourcing_offers",
    "set_part_eda",
    "upload_eda_asset",
    "import_vendor_zip",
    "fetch_lcsc",
    "add_stock",
    "consume_stock",
    "move_stock",
    "create_category",
}


def test_declared_write_set_matches_the_expected_list():
    """Change detection for the tool inventory — NOT a correctness proof.

    Both sides of this comparison come from the same `writes=` keyword,
    so it can only notice a tool being renamed, added or removed. It
    cannot notice a tool being declared WRONG: `sourcing_offers` shipped
    as a read tool while spending the workspace's provider budget, and
    an earlier version of this test passed the whole time, because the
    declaration it checked against was the wrong declaration.

    What actually holds the invariant is two things below:
    `test_read_tools_touch_nothing`, which runs every read tool and lets
    the database say whether it wrote, and the runtime guard in
    `principal.unit_of_work` that refuses the commit if it did.
    """
    declared = {spec.name for spec in load_tools() if spec.writes}
    assert declared == _EXPECTED_WRITE_TOOLS


def test_every_tool_declares_a_rate_ceiling():
    """`/mcp` gets no slowapi decorators, so the ceiling is per tool.

    A tool with no ceiling at all is an uncapped endpoint — which is
    what the whole surface was before `enforce_rate_limit`. Asserting
    that the expensive ones match their REST twins keeps the two doors
    priced the same.
    """
    rates = {spec.name: spec.rate for spec in load_tools()}
    assert rates["fetch_lcsc"] == "5/minute"  # api/routes/eda_import.py::_LCSC_RATE
    assert rates["import_vendor_zip"] == "10/minute"  # ::_IMPORT_RATE
    assert rates["upload_eda_asset"] == "20/minute"  # api/routes/eda.py::_UPLOAD_RATE
    assert rates["sourcing_offers"] == "60/minute"  # POST /api/sourcing/search
    assert all(spec.rate for spec in load_tools())


def test_every_tool_has_an_agent_facing_docstring():
    """The docstring is the contract the model reads; an empty one is a
    tool the model cannot use correctly."""
    for spec in load_tools():
        doc = (spec.fn.__doc__ or "").strip()
        assert len(doc) > 40, f"{spec.name} has no usable description"


async def test_read_only_token_still_sees_every_tool(readonly_token):
    """Discoverability is not the gate. A read-only token lists the write
    tools too, and learns it may not use one only by calling it — the
    alternative teaches the model the tool does not exist."""
    async with mcp_session(readonly_token) as s:
        listed = {t.name for t in (await s.list_tools()).tools}
    assert listed == {spec.name for spec in load_tools()}
    assert _EXPECTED_WRITE_TOOLS <= listed


async def test_full_token_lists_the_same_tools(full_token):
    async with mcp_session(full_token) as s:
        listed = {t.name for t in (await s.list_tools()).tools}
    assert listed == {spec.name for spec in load_tools()}


# ---------------------------------------------------------------------------
# The write declaration, checked against behaviour rather than itself
# ---------------------------------------------------------------------------


def _read_tool_arguments(client: TestClient) -> dict[str, dict]:
    """Valid arguments for every read tool, against a seeded workspace.

    Seeding matters: a tool called against an empty workspace returns
    early and never reaches the code that might write, so an empty
    fixture would make the test below pass vacuously.
    """
    part_id = create_part(client, "Cleanliness probe", mpn="CLEAN-1")
    create_storage(client, "Clean bin")
    client.post("/api/stock/add", json={"part_id": part_id, "quantity": 5})
    category = client.post("/api/categories", json={"name": "Clean cat"})
    assert category.status_code in (200, 201), category.text
    slug = category.json()["data"]["library_slug"]
    project = client.post("/api/projects", json={"name": "Clean board"})
    assert project.status_code in (200, 201), project.text
    project_id = project.json()["data"]["id"]
    entry = client.post(
        f"/api/projects/{project_id}/entries",
        json={"entry_type": "part", "part_id": part_id, "quantity": 2},
    )
    assert entry.status_code in (200, 201), entry.text

    return {
        "search_parts": {"query": "Cleanliness"},
        "get_part": {"id_or_mpn": part_id},
        "get_part_eda": {"part_id": part_id},
        "find_parts_missing_eda": {"kind": "footprint", "category_slug": slug},
        "stock_levels": {},
        "list_storage_locations": {},
        "list_categories": {},
        "list_projects": {},
        "get_project_bom": {"project_id": project_id},
        "bom_shortages": {"project_id": project_id, "build_qty": 3},
    }


async def test_read_tools_touch_nothing(authed_client, full_token):
    """Run every read tool and let the DATABASE decide whether it writes.

    This is the assertion the structural test cannot make. Each tool runs
    under `unit_of_work(writes=False)`, which watches the session's
    connection for any INSERT / UPDATE / DELETE and refuses to commit if
    it sees one — so a tool that quietly writes fails here with
    `mcp.undeclared_write` instead of shipping.

    The arguments table is checked against the registry so a new read
    tool cannot be added without being covered: an untested read tool is
    exactly the one that gets the declaration wrong.
    """
    arguments = _read_tool_arguments(authed_client)
    read_tools = {spec.name for spec in load_tools() if not spec.writes}
    assert set(arguments) == read_tools, (
        "every read tool needs arguments here — otherwise it is unchecked"
    )

    async with mcp_session(full_token) as s:
        for name, kwargs in sorted(arguments.items()):
            result = await s.call_tool(name, kwargs)
            assert not result.is_error, f"{name}: {_text(result)}"


@contextmanager
def temporary_tool(fn, *, writes: bool = False, rate: str | None = None, name=None):
    """Register a tool for the duration of a test, then take it away.

    Used for the cases that need a tool the product does not have: one
    that lies about writing, and one that blocks inside its own body.
    Both are properties of the machinery rather than of any real tool,
    and inventing a real one to test them would ship it to users.
    """
    from app.mcp.tools import _registry

    kwargs = {"writes": writes}
    if rate is not None:
        kwargs["rate"] = rate
    if name is not None:
        kwargs["name"] = name
    _registry.tool(**kwargs)(fn)
    spec = _registry.REGISTRY.pop()
    mcp_server._server.add_tool(spec.fn, name=spec.name)
    try:
        yield spec.name
    finally:
        mcp_server._server.remove_tool(spec.name)


async def test_a_read_tool_that_writes_is_refused_and_rolled_back(
    authed_client, full_token, db
):
    """The runtime guard, on a tool built to violate it.

    Two things asserted, and the second is the one that matters: the
    caller gets a clean `mcp.undeclared_write` rather than a crash, AND
    the row the tool tried to create is not there. A guard that reported
    the problem after committing would be worse than none.
    """
    part_id = create_part(authed_client, "Guard probe")
    before = db.query(StockEntry).filter(StockEntry.part_id == uuid.UUID(part_id)).count()

    def sneaky_write(caller, part_id: str) -> dict:
        """Declared read-only, writes anyway. Test fixture."""
        from app.domain.stock.schemas import AddStockIn
        from app.domain.stock.service import add_stock

        add_stock(
            caller.db,
            workspace_id=caller.ws.id,
            user_id=caller.user.id,
            payload=AddStockIn(part_id=uuid.UUID(part_id), quantity=3),
        )
        return {"ok": True}

    with temporary_tool(sneaky_write) as tool_name:
        async with mcp_session(full_token) as s:
            message = await call_error(s, tool_name, part_id=part_id)

    assert "mcp.undeclared_write" in message
    assert "do not retry" in message
    db.rollback()
    after = db.query(StockEntry).filter(StockEntry.part_id == uuid.UUID(part_id)).count()
    assert after == before


async def test_the_guard_also_catches_a_core_level_insert(authed_client, full_token, db):
    """ORM state alone would have missed the bug that motivated this.

    `domain/sourcing/cache.py::get_or_fetch` writes with a Core
    `pg_insert(...).on_conflict_do_update`, which never enters the
    session's unit of work — `Session.new` / `.dirty` / `.deleted` stay
    empty through it. The guard watches statements on the connection for
    exactly this reason, so this test uses the same shape of write.
    """
    def core_write(caller) -> dict:
        """Declared read-only, writes via Core SQL. Test fixture."""
        from sqlalchemy import text

        caller.db.execute(
            text(
                "INSERT INTO part_categories "
                "(id, workspace_id, name, library_slug, sort_order) "
                "VALUES (gen_random_uuid(), :ws, 'guard-probe', 'guard-probe', 0)"
            ),
            {"ws": caller.ws.id},
        )
        return {"ok": True}

    with temporary_tool(core_write) as tool_name:
        async with mcp_session(full_token) as s:
            message = await call_error(s, tool_name)

    assert "mcp.undeclared_write" in message
    db.rollback()
    listed = authed_client.get("/api/categories").json()["data"]
    assert [row["name"] for row in listed] == []


# ---------------------------------------------------------------------------
# The write gate
# ---------------------------------------------------------------------------


async def test_read_only_token_is_refused_a_write(authed_client, readonly_token):
    part_id = create_part(authed_client, "RO gate part")
    async with mcp_session(readonly_token) as s:
        message = await call_error(
            s, "set_part_eda", part_id=part_id, symbol_ref_external="Device:R"
        )
    assert "auth.token_read_only" in message


async def test_read_only_token_may_still_read(authed_client, readonly_token):
    create_part(authed_client, "RO readable part", mpn="RO-READ-1")
    async with mcp_session(readonly_token) as s:
        part = await call(s, "get_part", id_or_mpn="RO-READ-1")
    assert part["name"] == "RO readable part"


async def test_viewer_role_token_is_refused_a_write(authed_client):
    part_id = create_part(authed_client, "Viewer gate part")
    viewer = _join_workspace(authed_client, "viewer")
    async with mcp_session(_mint(viewer)["token"]) as s:
        message = await call_error(
            s, "set_part_eda", part_id=part_id, symbol_ref_external="Device:R"
        )
    assert "resource.insufficient_role" in message


async def test_viewer_role_token_may_still_read(authed_client):
    create_part(authed_client, "Viewer readable", mpn="VIEW-READ-1")
    viewer = _join_workspace(authed_client, "viewer")
    async with mcp_session(_mint(viewer)["token"]) as s:
        part = await call(s, "get_part", id_or_mpn="VIEW-READ-1")
    assert part["mpn"] == "VIEW-READ-1"


async def test_member_role_token_may_write(authed_client):
    part_id = create_part(authed_client, "Member writable")
    member = _join_workspace(authed_client, "member")
    async with mcp_session(_mint(member)["token"]) as s:
        result = await call(
            s, "set_part_eda", part_id=part_id, symbol_ref_external="Device:R"
        )
    assert result["symbol_ref_external"] == "Device:R"


async def test_price_lookup_needs_a_full_token(authed_client, readonly_token):
    """`sourcing_offers` reads, but it spends — so it gates like a write.

    A miss on the cache costs a call against the workspace's metered
    distributor quota and leaves a `sourcing_cache` row behind. The REST
    twin (`POST /api/sourcing/search`) sits behind
    `require_role("member")` and refuses a read-only token; this asserts
    the MCP door charges the same admission.
    """
    create_part(authed_client, "Priced part", mpn="PRICED-1")
    async with mcp_session(readonly_token) as s:
        message = await call_error(s, "sourcing_offers", part_id="PRICED-1")
    assert "auth.token_read_only" in message


async def test_price_lookup_is_refused_to_a_viewer(authed_client):
    create_part(authed_client, "Viewer priced", mpn="VIEW-PRICED-1")
    viewer = _join_workspace(authed_client, "viewer")
    async with mcp_session(_mint(viewer)["token"]) as s:
        message = await call_error(s, "sourcing_offers", part_id="VIEW-PRICED-1")
    assert "resource.insufficient_role" in message


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


@pytest.fixture
def rate_limits_on():
    """Turn slowapi on for one test and leave a clean bucket store.

    The limiter is disabled outside prod (`core/ratelimit.py`) so the
    suite can hammer endpoints, which means the MCP ceilings are inert
    in every other test here. Same toggle `test_sourcing_alerts_route.py`
    uses.
    """
    from app.core.ratelimit import limiter

    limiter.reset()
    limiter.enabled = True
    try:
        yield
    finally:
        limiter.enabled = False
        limiter.reset()


async def test_a_tool_over_its_ceiling_is_refused(full_token, rate_limits_on, db):
    """The ceiling is enforced per tool, and it refuses cleanly.

    `/mcp` is one opaque ASGI mount, so slowapi's route decorators never
    run for it — every tool was uncapped, including the ones that reach
    third parties. Asserted on a throwaway tool with a ceiling of one so
    the test does not depend on any real tool's number.
    """
    def cheap(caller) -> dict:
        """Throwaway tool for the rate-limit test."""
        return {"ok": True}

    with temporary_tool(cheap, rate="1/minute", name="rate_probe") as tool_name:
        async with mcp_session(full_token) as s:
            assert (await call(s, tool_name))["ok"] is True
            message = await call_error(s, tool_name)

    assert "rate_limited" in message
    assert "rate_probe" in message


async def test_each_tool_gets_its_own_bucket(full_token, rate_limits_on, db):
    """Exhausting one tool must not exhaust another.

    Otherwise a burst of cheap `search_parts` calls would consume the
    five-per-minute allowance `fetch_lcsc` has for reaching EasyEDA,
    which is the opposite of what per-tool ceilings are for.
    """
    def probe_a(caller) -> dict:
        """Throwaway tool A."""
        return {"tool": "a"}

    def probe_b(caller) -> dict:
        """Throwaway tool B."""
        return {"tool": "b"}

    with temporary_tool(probe_a, rate="1/minute", name="bucket_a") as a:
        with temporary_tool(probe_b, rate="1/minute", name="bucket_b") as b:
            async with mcp_session(full_token) as s:
                await call(s, a)
                assert "rate_limited" in await call_error(s, a)
                # B's bucket is untouched by A's burst.
                assert (await call(s, b))["tool"] == "b"


async def test_rate_buckets_are_per_workspace(db, rate_limits_on):
    """One tenant's burst must not spend another tenant's allowance."""
    a, b = TestClient(app), TestClient(app)
    signup_user(a)
    signup_user(b)
    token_a, token_b = _mint(a)["token"], _mint(b)["token"]

    def probe(caller) -> dict:
        """Throwaway tool for the per-workspace bucket test."""
        return {"ws": str(caller.ws.id)}

    with temporary_tool(probe, rate="1/minute", name="ws_bucket") as tool_name:
        async with mcp_server_running():
            async with mcp_client(token_a) as sa:
                await call(sa, tool_name)
                assert "rate_limited" in await call_error(sa, tool_name)
            async with mcp_client(token_b) as sb:
                assert (await call(sb, tool_name))["ws"]


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


async def test_search_parts_matches_and_reports_truncation(authed_client, full_token):
    for n in range(3):
        create_part(authed_client, f"MCP search widget {n}")
    create_part(authed_client, "unrelated thing")
    async with mcp_session(full_token) as s:
        found = await call(s, "search_parts", query="MCP search")
        capped = await call(s, "search_parts", query="MCP search", limit=2)
    assert len(found["parts"]) == 3
    assert found["truncated"] is False
    assert len(capped["parts"]) == 2
    assert capped["truncated"] is True
    assert all(p["part_url"].endswith(p["id"]) for p in found["parts"])


async def test_get_part_resolves_by_id_and_by_mpn(authed_client, full_token):
    part_id = create_part(authed_client, "Dual lookup", mpn="DUAL-1")
    async with mcp_session(full_token) as s:
        by_id = await call(s, "get_part", id_or_mpn=part_id)
        by_mpn = await call(s, "get_part", id_or_mpn="DUAL-1")
    assert by_id["id"] == by_mpn["id"] == part_id


async def test_get_part_reports_stock_and_storage_names(authed_client, full_token):
    part_id = create_part(authed_client, "Stocked part")
    bin_id = create_storage(authed_client, "Bin MCP-7")
    authed_client.post(
        "/api/stock/add",
        json={"part_id": part_id, "quantity": 12, "storage_location_id": bin_id},
    )
    async with mcp_session(full_token) as s:
        part = await call(s, "get_part", id_or_mpn=part_id)
    assert part["stock"]["on_hand"] == 12
    assert part["stock"]["available"] == 12
    assert part["stock"]["locations"][0]["storage_location_name"] == "Bin MCP-7"


async def test_get_part_splits_catalog_metadata_from_specs(
    authed_client, full_token, db
):
    """`image_url` is plumbing; `Tolerance` is a specification.

    A model handed one flat bag of custom fields will quote an asset URL
    as a part attribute, so the split is part of the tool's contract.
    The catalog row is inserted directly because
    `POST /api/custom-fields` refuses the provider-reserved keys — they
    are written by the provider import, never by hand.
    """
    part_id = create_part(authed_client, "Split fields part")
    r = authed_client.post(
        "/api/custom-fields",
        json={
            "object_type": "part",
            "object_id": part_id,
            "key": "Tolerance",
            "value": "1%",
        },
    )
    assert r.status_code == 201, r.text

    part = db.get(Part, uuid.UUID(part_id))
    db.add(
        CustomField(
            workspace_id=part.workspace_id,
            object_type="part",
            object_id=part.id,
            key="image_url",
            value="/api/parts/assets/x.png",
            source="provider",
        )
    )
    db.flush()

    async with mcp_session(full_token) as s:
        result = await call(s, "get_part", id_or_mpn=part_id)
    assert result["specs"] == {"Tolerance": "1%"}
    assert "image_url" in result["catalog_fields"]
    assert "image_url" not in result["specs"]


async def test_get_part_unknown_is_a_clean_tool_error(full_token, db):
    async with mcp_session(full_token) as s:
        message = await call_error(s, "get_part", id_or_mpn="no-such-part")
    assert "part.not_found" in message
    assert "Traceback" not in message


async def test_find_parts_missing_eda(authed_client, full_token):
    with_symbol = create_part(authed_client, "Has symbol")
    create_part(authed_client, "Needs symbol")
    async with mcp_session(full_token) as s:
        await call(s, "set_part_eda", part_id=with_symbol, symbol_ref_external="Device:R")
        missing = await call(s, "find_parts_missing_eda", kind="symbol")
    names = {p["name"] for p in missing["parts"]}
    assert "Needs symbol" in names
    assert "Has symbol" not in names


async def test_find_parts_missing_eda_batches_its_lookups(authed_client, full_token):
    """The status lookup is batched, so the query count is flat in part count.

    Before, this tool asked two queries per part scanned — the N+1 that
    made "what still needs a footprint?" cost a round trip per row of the
    library. Counted rather than described, because a refactor that
    reintroduces the per-part call would still pass a behavioural test.
    """
    for n in range(12):
        create_part(authed_client, f"Batch probe {n}")

    from sqlalchemy import event
    from sqlalchemy.orm import Session

    statements: list[str] = []

    def record(state) -> None:
        statements.append(str(state.statement))

    # Listened on the ORM `Session` class rather than an engine: the test
    # fixture binds sessions to its OWN engine, so an engine-level
    # listener attached to `app.infra.db`'s would never fire.
    event.listen(Session, "do_orm_execute", record)
    try:
        async with mcp_session(full_token) as s:
            missing = await call(s, "find_parts_missing_eda", kind="footprint")
    finally:
        event.remove(Session, "do_orm_execute", record)

    assert len(missing["parts"]) == 12
    part_eda_queries = [q for q in statements if "part_eda" in q]
    # One for the whole page, not one per part. (Zero footprints are
    # configured, so the second batched query is skipped entirely.)
    assert len(part_eda_queries) == 1, part_eda_queries


async def test_find_parts_missing_eda_bounds_the_scan(authed_client, full_token, monkeypatch):
    """The SCAN is capped, not only the result.

    A workspace where every part already has a footprint returns nothing
    — and would still have read the whole parts table to work that out.
    The cap is lowered here rather than seeding thousands of parts.
    """
    from app.mcp.tools import read as read_tools

    monkeypatch.setattr(read_tools, "_SCAN_CAP", 3)
    for n in range(6):
        create_part(authed_client, f"Scan cap probe {n}")

    async with mcp_session(full_token) as s:
        missing = await call(s, "find_parts_missing_eda", kind="symbol")

    assert missing["scanned"] == 3
    assert len(missing["parts"]) == 3
    assert missing["truncated"] is True


async def test_stock_levels_low_stock_only(authed_client, full_token):
    low = create_part(authed_client, "Low part", low_stock_report_quantity=10)
    high = create_part(authed_client, "High part", low_stock_report_quantity=1)
    for part_id, qty in ((low, 2), (high, 50)):
        authed_client.post("/api/stock/add", json={"part_id": part_id, "quantity": qty})
    async with mcp_session(full_token) as s:
        result = await call(s, "stock_levels", low_stock_only=True)
    names = {p["name"] for p in result["parts"]}
    assert names == {"Low part"}


async def test_stock_levels_is_bounded_and_reports_reserved(authed_client, full_token):
    """The no-arguments call is paged, and `reserved` still comes out right.

    `stock_levels()` with no part is the call an agent makes to "check
    stock", and it used to load every part in the workspace and then ask
    one aggregate query per row for the reserved figure. Now it is one
    page and two grouped queries — this pins both the page boundary and
    that batching `reserved` did not change the number.
    """
    for n in range(5):
        part_id = create_part(authed_client, f"Paged part {n:02d}")
        authed_client.post("/api/stock/add", json={"part_id": part_id, "quantity": 4})

    async with mcp_session(full_token) as s:
        page = await call(s, "stock_levels", limit=3)
        everything = await call(s, "stock_levels", limit=50)

    assert len(page["parts"]) == 3
    assert page["truncated"] is True
    assert len(everything["parts"]) == 5
    assert everything["truncated"] is False
    assert all(row["on_hand"] == 4 for row in everything["parts"])
    assert all(row["available"] == 4 for row in everything["parts"])
    assert all(row.get("reserved", 0) == 0 for row in everything["parts"])


async def test_search_treats_percent_as_a_literal(authed_client, full_token):
    """`%` is a LIKE wildcard, and an agent searching for "10%" means 10%.

    Unescaped, the query matched every part whose name merely starts with
    "10" — a wrong answer rather than an error, which is the failure mode
    that survives longest.
    """
    create_part(authed_client, "Divider 10% tolerance")
    create_part(authed_client, "Divider 100 ohm")

    async with mcp_session(full_token) as s:
        found = await call(s, "search_parts", query="10%")
        underscore = await call(s, "search_parts", query="10_0")

    assert [p["name"] for p in found["parts"]] == ["Divider 10% tolerance"]
    assert underscore["parts"] == []


async def test_list_storage_and_categories(authed_client, full_token):
    create_storage(authed_client, "Shelf A")
    async with mcp_session(full_token) as s:
        storage = await call(s, "list_storage_locations")
        category = await call(s, "create_category", name="Resistors")
        categories = await call(s, "list_categories")
    assert [row["name"] for row in storage["storage_locations"]] == ["Shelf A"]
    assert category["slug"] == "resistors"
    assert [row["slug"] for row in categories["categories"]] == ["resistors"]


async def test_project_bom_and_shortages(authed_client, full_token):
    part_id = create_part(authed_client, "BOM part")
    project = authed_client.post("/api/projects", json={"name": "MCP board"})
    assert project.status_code in (200, 201), project.text
    project_id = project.json()["data"]["id"]
    entry = authed_client.post(
        f"/api/projects/{project_id}/entries",
        json={
            "entry_type": "part",
            "part_id": part_id,
            "quantity": 4,
            "designators": ["R1", "R2", "R3", "R4"],
        },
    )
    assert entry.status_code in (200, 201), entry.text

    async with mcp_session(full_token) as s:
        projects = await call(s, "list_projects")
        bom = await call(s, "get_project_bom", project_id=project_id)
        short = await call(s, "bom_shortages", project_id=project_id, build_qty=2)

    assert [p["name"] for p in projects["projects"]] == ["MCP board"]
    assert bom["lines"][0]["quantity"] == 4
    assert bom["lines"][0]["designators"] == ["R1", "R2", "R3", "R4"]
    assert short["shortages"][0]["required"] == 8
    assert short["shortages"][0]["short_by"] == 8


async def test_sourcing_offers_degrades_when_unconfigured(authed_client, full_token):
    """Not-configured is an answer, not a failure — an agent that gets a
    tool error here will try to route around it."""
    create_part(authed_client, "Unsourced", mpn="UNSOURCED-1")
    async with mcp_session(full_token) as s:
        result = await call(s, "sourcing_offers", part_id="UNSOURCED-1")
    assert result["status"] == "not_configured"
    assert result["offers"] == []


async def test_sourcing_offers_without_mpn_says_so(authed_client, full_token):
    part_id = create_part(authed_client, "No MPN part")
    async with mcp_session(full_token) as s:
        result = await call(s, "sourcing_offers", part_id=part_id)
    assert result["status"] == "no_mpn"


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------


async def test_set_part_eda_round_trips(authed_client, full_token):
    part_id = create_part(authed_client, "EDA round trip")
    async with mcp_session(full_token) as s:
        written = await call(
            s,
            "set_part_eda",
            part_id=part_id,
            symbol_ref_external="Device:R",
            footprint_ref_external="Resistor_SMD:R_0603",
            value="10k",
            keywords="resistor",
            footprint_filters=["R_0603*"],
        )
        read_back = await call(s, "get_part_eda", part_id=part_id)
    assert written == read_back
    assert read_back["value"] == "10k"
    assert read_back["footprint_filters"] == ["R_0603*"]
    assert read_back["configured"] is True


async def test_set_part_eda_rejects_a_conflicting_reference(authed_client, full_token):
    """The XOR is the service's rule; the tool must surface it, not
    bypass it or crash on it."""
    part_id = create_part(authed_client, "Conflicting refs")
    symbol = _upload_symbol(authed_client, "MCPSYM")
    async with mcp_session(full_token) as s:
        message = await call_error(
            s,
            "set_part_eda",
            part_id=part_id,
            symbol_id=symbol,
            symbol_ref_external="Device:R",
        )
    assert "eda.ref_conflict" in message


def _upload_symbol(client: TestClient, name: str) -> str:
    r = client.post(
        "/api/eda/symbols",
        files={"file": (f"{name}.kicad_sym", _symbol_text(name).encode(), "text/plain")},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


async def test_get_part_eda_unconfigured_says_so(authed_client, full_token):
    part_id = create_part(authed_client, "Unconfigured")
    async with mcp_session(full_token) as s:
        result = await call(s, "get_part_eda", part_id=part_id)
    assert result["configured"] is False


async def test_upload_eda_asset_creates_and_wires(authed_client, full_token, db):
    part_id = create_part(authed_client, "Upload target")
    async with mcp_session(full_token) as s:
        created = await call(
            s,
            "upload_eda_asset",
            kind="symbol",
            filename="MCPUP.kicad_sym",
            content_base64=_b64(_symbol_text("MCPUP").encode()),
            part_id=part_id,
        )
        config = await call(s, "get_part_eda", part_id=part_id)
    assert created["created"] is True
    assert created["name"] == "MCPUP"
    assert created["part_eda_updated"] is True
    assert config["symbol_id"] == created["id"]
    assert db.get(EdaSymbol, uuid.UUID(created["id"])) is not None


async def test_upload_eda_asset_is_idempotent_on_identical_bytes(
    authed_client, full_token
):
    async with mcp_session(full_token) as s:
        payload = dict(
            kind="footprint",
            filename="MCPFP.kicad_mod",
            content_base64=_b64(_footprint_text("MCPFP").encode()),
        )
        first = await call(s, "upload_eda_asset", **payload)
        second = await call(s, "upload_eda_asset", **payload)
    assert first["created"] is True
    assert second["created"] is False
    assert first["id"] == second["id"]


async def test_upload_eda_asset_validates_the_payload(full_token, db):
    async with mcp_session(full_token) as s:
        bad_base64 = await call_error(
            s,
            "upload_eda_asset",
            kind="symbol",
            filename="x.kicad_sym",
            content_base64="not base64 at all!!",
        )
        wrong_kind = await call_error(
            s,
            "upload_eda_asset",
            kind="model3d",
            filename="x.step",
            content_base64=_b64(b"definitely not a STEP file"),
        )
    assert "eda.invalid_file" in bad_base64
    assert "eda.invalid_file" in wrong_kind or "eda.unsupported_kind" in wrong_kind


async def test_upload_eda_asset_enforces_the_size_cap(full_token, db):
    """The cap is the BOM-import lane's 4 MiB, measured on the DECODED
    bytes — a base64 argument must not buy 33% more than an upload."""
    from app.mcp.tools._shared import MAX_DECODED_BYTES

    oversized = _b64(b"A" * (MAX_DECODED_BYTES + 1024))
    async with mcp_session(full_token) as s:
        message = await call_error(
            s,
            "upload_eda_asset",
            kind="model3d",
            filename="huge.step",
            content_base64=oversized,
        )
    assert "eda.file_too_large" in message


async def test_import_vendor_zip_matches_the_rest_path(authed_client, full_token, db):
    """Same archive, two doors, same rows.

    The REST importer and the MCP tool call `importer.import_plan` with
    the same plan, so the library they produce must be identical — this
    is the assertion that keeps the MCP tool from quietly becoming a
    second, divergent import pipeline.
    """
    raw = _snapeda_zip("MCPZIP")
    via_rest_part = create_part(authed_client, "Zip via REST", mpn="ZIP-REST")
    rest = authed_client.post(
        f"/api/parts/{via_rest_part}/eda/import",
        files={"file": ("LIB_MCPZIP.zip", raw, "application/zip")},
    )
    assert rest.status_code == 200, rest.text
    rest_data = rest.json()["data"]

    via_mcp_part = create_part(authed_client, "Zip via MCP", mpn="ZIP-MCP")
    async with mcp_session(full_token) as s:
        mcp_data = await call(
            s, "import_vendor_zip", part_id=via_mcp_part, content_base64=_b64(raw)
        )

    assert mcp_data["vendor"] == rest_data["vendor"] == "snapeda"
    assert mcp_data["part_eda_updated"] is True
    # The second import reuses the identical content-addressed rows
    # rather than creating a parallel set.
    assert mcp_data["created"] == 0
    assert mcp_data["reused"] == 3
    assert mcp_data["symbols"][0]["id"] == rest_data["symbol"]["id"]
    assert mcp_data["footprints"][0]["id"] == rest_data["footprint"]["id"]


async def test_add_and_consume_stock_go_through_the_ledger(
    authed_client, full_token, db
):
    part_id = create_part(authed_client, "Ledger part")
    bin_id = create_storage(authed_client, "Ledger bin")
    async with mcp_session(full_token) as s:
        added = await call(
            s, "add_stock", part_id=part_id, qty=25, storage_location_id=bin_id
        )
        # The location is not optional in practice: stock is tracked per
        # location, so consuming without one draws on the unassigned
        # pool and finds nothing.
        consumed = await call(
            s,
            "consume_stock",
            part_id=part_id,
            qty=5,
            storage_location_id=bin_id,
            note="build",
        )
        levels = await call(s, "stock_levels", part_id=part_id)
    assert added["on_hand"] == 25
    assert consumed["on_hand"] == 20
    assert consumed["quantity_delta"] == -5
    assert levels["parts"][0]["available"] == 20


async def test_consume_without_a_location_draws_on_the_unassigned_pool(
    authed_client, full_token
):
    """Omitting the location is not "consume from anywhere".

    `remove_stock` matches the NULL storage bucket exactly, so a part
    whose stock all sits in a bin has nothing to consume without one.
    Pinned because the alternative reading is the one an agent will
    assume, and the tool's docstring has to keep warning about it.
    """
    part_id = create_part(authed_client, "Binned only")
    bin_id = create_storage(authed_client, "Only bin")
    authed_client.post(
        "/api/stock/add",
        json={"part_id": part_id, "quantity": 6, "storage_location_id": bin_id},
    )
    async with mcp_session(full_token) as s:
        message = await call_error(s, "consume_stock", part_id=part_id, qty=1)
    assert "have 0" in message


async def test_consume_more_than_available_is_refused(authed_client, full_token):
    part_id = create_part(authed_client, "Thin stock")
    authed_client.post("/api/stock/add", json={"part_id": part_id, "quantity": 3})
    async with mcp_session(full_token) as s:
        message = await call_error(s, "consume_stock", part_id=part_id, qty=10)
    assert "stock.operation_error" in message
    assert "have 3" in message


async def test_move_stock_between_locations(authed_client, full_token):
    part_id = create_part(authed_client, "Movable")
    source = create_storage(authed_client, "From bin")
    target = create_storage(authed_client, "To bin")
    authed_client.post(
        "/api/stock/add",
        json={"part_id": part_id, "quantity": 8, "storage_location_id": source},
    )
    async with mcp_session(full_token) as s:
        moved = await call(
            s,
            "move_stock",
            part_id=part_id,
            qty=3,
            from_location_id=source,
            to_location_id=target,
        )
        part = await call(s, "get_part", id_or_mpn=part_id)
    assert moved["on_hand"] == 8
    by_name = {
        row["storage_location_name"]: row["quantity"]
        for row in part["stock"]["locations"]
    }
    assert by_name == {"From bin": 5, "To bin": 3}


async def test_non_positive_quantity_is_refused(authed_client, full_token):
    part_id = create_part(authed_client, "Zero qty")
    async with mcp_session(full_token) as s:
        message = await call_error(s, "add_stock", part_id=part_id, qty=0)
    assert "stock.operation_error" in message


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def _audit_rows(db, action: str) -> list[AuditLog]:
    return list(
        db.query(AuditLog).filter(AuditLog.action == action).order_by(AuditLog.id).all()
    )


async def test_mutation_audit_row_names_the_token_owner(
    authed_client, full_token, db
):
    """An agent is a person's credential, so the trail names the person.

    Also pins the action name and comment grammar against the REST
    route's, which is what stops the two surfaces telling an auditor
    different stories about the same operation.
    """
    owner_id = uuid.UUID(authed_client.get("/api/auth/me").json()["data"]["user"]["id"])
    part_id = create_part(authed_client, "Audited part")
    async with mcp_session(full_token) as s:
        await call(s, "set_part_eda", part_id=part_id, symbol_ref_external="Device:R")

    rows = _audit_rows(db, "part_eda.updated")
    assert len(rows) == 1
    assert rows[0].user_id == owner_id
    assert rows[0].target_type == "part_eda"
    assert rows[0].target_ids == [uuid.UUID(part_id)]
    assert rows[0].comment == "fields=symbol_ref_external"


async def test_upload_and_import_write_the_same_actions_as_the_routes(
    authed_client, full_token, db
):
    part_id = create_part(authed_client, "Import audited")
    async with mcp_session(full_token) as s:
        await call(
            s,
            "upload_eda_asset",
            kind="symbol",
            filename="AUDSYM.kicad_sym",
            content_base64=_b64(_symbol_text("AUDSYM").encode()),
        )
        await call(
            s,
            "import_vendor_zip",
            part_id=part_id,
            content_base64=_b64(_snapeda_zip("AUDZIP")),
        )

    assert len(_audit_rows(db, "eda_symbol.uploaded")) == 2
    assert len(_audit_rows(db, "eda_footprint.uploaded")) == 1
    assert len(_audit_rows(db, "eda_datafile.uploaded")) == 1
    imported = _audit_rows(db, "part_eda.imported")
    assert len(imported) == 1
    assert imported[0].comment == "vendor=snapeda,files=3"
    assert _audit_rows(db, "eda_datafile.uploaded")[0].comment.startswith("kind=step,")


async def test_category_creation_audit_matches_the_route(authed_client, full_token, db):
    async with mcp_session(full_token) as s:
        await call(s, "create_category", name="Capacitors", description="passives")
    rows = _audit_rows(db, "category.created")
    assert len(rows) == 1
    assert rows[0].target_type == "part_category"
    assert rows[0].comment == "fields=description,name"


async def test_stock_writes_no_audit_row_but_the_ledger_names_the_owner(
    authed_client, full_token, db
):
    """Parity with the REST path, which audits no stock movement either.

    `stock_entries` is append-only and carries `created_by`, so it IS
    the record. An audit row here and none on `/api/stock/add` would
    make the two surfaces disagree about what a stock movement is.
    """
    owner_id = uuid.UUID(authed_client.get("/api/auth/me").json()["data"]["user"]["id"])
    part_id = create_part(authed_client, "Ledger audit part")
    async with mcp_session(full_token) as s:
        await call(s, "add_stock", part_id=part_id, qty=7)

    assert _audit_rows(db, "stock.added") == []
    entries = (
        db.query(StockEntry)
        .filter(StockEntry.part_id == uuid.UUID(part_id))
        .all()
    )
    assert len(entries) == 1
    assert entries[0].quantity_delta == 7
    assert entries[0].created_by == owner_id


async def test_refused_write_leaves_no_trace(authed_client, readonly_token, db):
    """A refusal must roll back, not half-commit."""
    part_id = create_part(authed_client, "Refused write part")
    async with mcp_session(readonly_token) as s:
        await call_error(s, "add_stock", part_id=part_id, qty=5)
    assert (
        db.query(StockEntry).filter(StockEntry.part_id == uuid.UUID(part_id)).count()
        == 0
    )


# ---------------------------------------------------------------------------
# The principal contextvar
# ---------------------------------------------------------------------------


@pytest.mark.real_db
async def test_concurrent_calls_do_not_cross_tenants(db):
    """Two tokens, overlapping calls, no bleed.

    The principal rides a contextvar set by the ASGI wrapper, which is
    only safe because the transport is STATELESS and handles each
    request inline in its caller's context. If someone switches the
    server to stateful mode, request handling moves into a task group
    started at lifespan time, the contextvar stops propagating, and this
    test is what says so.

    `real_db` for the same reason `test_stock_concurrency.py` uses it:
    the default fixture shares ONE connection across the whole test and
    isolates with savepoints, so two genuinely concurrent sessions
    interleave `SAVEPOINT`/`RELEASE` on it and the transaction aborts.
    That is a property of the harness, not of the server — in production
    every request takes its own connection from the pool.
    """
    a, b = TestClient(app), TestClient(app)
    signup_user(a)
    signup_user(b)
    create_part(a, "Tenant A part", mpn="TENANT-A")
    create_part(b, "Tenant B part", mpn="TENANT-B")
    token_a, token_b = _mint(a)["token"], _mint(b)["token"]

    seen: dict[str, list[str]] = {}

    async def probe(label: str, token: str) -> None:
        async with mcp_client(token) as s:
            found = await call(s, "search_parts", query="Tenant")
            seen[label] = [p["mpn"] for p in found["parts"]]

    # One server, two clients — the shape production has. Starting a
    # lifespan per client would race on the module-level dispatcher
    # handle rather than testing anything about tenancy.
    async with mcp_server_running():
        async with anyio.create_task_group() as tg:
            tg.start_soon(probe, "a", token_a)
            tg.start_soon(probe, "b", token_b)

    assert seen["a"] == ["TENANT-A"]
    assert seen["b"] == ["TENANT-B"]


@pytest.mark.real_db
async def test_overlapping_tool_bodies_see_their_own_principal(db):
    """The same property, with the overlap FORCED rather than hoped for.

    The test above starts two calls concurrently, but nothing makes them
    actually overlap: the first can finish before the second begins, and
    then it proves nothing about contextvars at all — it would pass
    against a global variable holding the last-authenticated tenant.

    Here a barrier inside the tool body holds each call open until both
    have arrived, so the two tool invocations are provably in flight at
    the same moment. Each then reads the principal and reports the
    workspace it sees. If the principal were shared state rather than
    per-context, both would report the same one.

    The probe itself does no database work — this is a question about the
    contextvar. `real_db` is still needed because AUTHENTICATION does:
    two overlapping calls mean two overlapping auth sessions, and the
    default fixture's single savepoint-shared connection cannot carry
    those. Same constraint as the sibling test above.
    """
    import threading

    from app.mcp import principal as principal_mod

    a, b = TestClient(app), TestClient(app)
    signup_user(a)
    signup_user(b)
    token_a, token_b = _mint(a)["token"], _mint(b)["token"]
    ws_a = a.get("/api/auth/me").json()["data"]["workspaces"][0]["id"]
    ws_b = b.get("/api/auth/me").json()["data"]["workspaces"][0]["id"]
    assert ws_a != ws_b

    barrier = threading.Barrier(2, timeout=10)

    def whose_workspace(caller) -> dict:
        """Blocks until both callers are inside, then reports its tenant."""
        barrier.wait()
        # Read from the contextvar, NOT from `caller` — `caller` is a
        # local and would be per-call however the principal were stored.
        return {"workspace_id": str(principal_mod.current().workspace_id)}

    seen: dict[str, str] = {}

    async def probe(label: str, token: str) -> None:
        async with mcp_client(token) as s:
            result = await call(s, "whose_workspace")
            seen[label] = result["workspace_id"]

    with temporary_tool(whose_workspace, name="whose_workspace"):
        async with mcp_server_running():
            async with anyio.create_task_group() as tg:
                tg.start_soon(probe, "a", token_a)
                tg.start_soon(probe, "b", token_b)

    assert seen == {"a": ws_a, "b": ws_b}
