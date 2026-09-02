"""Agent REST enablement — the whole API under PAT header auth.

`test_api_tokens.py` proves the credential works. This module proves the
*product* works through it: one agent walks the real surface — categories,
parts, EDA uploads, the ledger, storage, search, reports, audit — holding
nothing but an `Authorization` header, and every step has to behave exactly
as it does for a browser session.

Three properties are pinned, and each one is a claim `docs/api/agents.md`
makes to machine consumers:

1. **Parity.** A token-authed walk produces the same rows, the same
   envelope and the same audit attribution as a cookie-authed one.
2. **No Origin needed.** The walk client sends no `Origin` header at all
   (conftest injects one into every `TestClient`, so it is stripped —
   see `_agent_client`). An agent is not a browser and has no origin to
   declare; `CsrfOriginMiddleware`'s exemption for `Authorization`-bearing
   requests is what makes that legal (ADR-0029).
3. **The blocked surfaces stay blocked.** The session-only list is read
   out of the app's own dependency graph rather than hand-copied, so a
   route that gains or loses `forbid_api_token` fails a test here
   instead of quietly changing what a leaked token can do.

Overlap with `test_api_tokens.py` is deliberate on (3): that module probes
a hand-written list of blocked routes, this one probes the list the code
actually has. The two disagree only when someone adds a guarded route and
forgets the probe — which is the failure worth catching.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.deps import forbid_api_token
from app.domain.audit.models import AuditLog
from app.domain.stock.models import StockEntry
from app.main import app
from tests._factories import signup_user

# ---------------------------------------------------------------------------
# Fixture content — the smallest inputs each upload lane accepts.
# Mirrors tests/test_eda.py; kept local so a change there fails loudly here
# rather than silently weakening this module's uploads.
# ---------------------------------------------------------------------------

SYMBOL_TEXT = (
    '(symbol "R_10k" (in_bom yes) (on_board yes)\n'
    '  (property "Reference" "R" (at 0 0 0))\n'
    '  (property "Value" "10k" (at 0 0 0))\n'
    ")\n"
)
FOOTPRINT_TEXT = (
    '(footprint "R_0402" (layer "F.Cu")\n'
    '  (descr "agent smoke")\n'
    '  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))\n'
    ")\n"
)
STEP_BYTES = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_client(token: str) -> TestClient:
    """A client that is *only* an API token: no cookies, no `Origin`.

    conftest patches `TestClient.__init__` to send `Origin: http://testserver`
    on every request, so the header is stripped here — the same technique
    `test_api_tokens.py::_strip_origin` uses. Every request this client makes
    therefore exercises the CSRF exemption, which is why §3 of the module
    docstring needs no separate test per write.
    """
    c = TestClient(app)
    c.headers.pop("origin", None)
    c.headers["Authorization"] = f"Token {token}"
    return c


def _mint(client: TestClient, **body: Any) -> dict:
    body.setdefault("label", "agent smoke")
    r = client.post("/api/tokens", json=body)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _code(response) -> str | None:
    return response.json().get("code")


def _envelope(response, expected: int | tuple[int, ...] = (200, 201)) -> dict:
    """Assert the status and the `{data, status}` envelope, return `data`."""
    allowed = (expected,) if isinstance(expected, int) else expected
    assert response.status_code in allowed, response.text
    body = response.json()
    assert set(body) >= {"data", "status"}, body
    return body["data"]


def _upload(client: TestClient, path: str, filename: str, content, **form: Any):
    if isinstance(content, str):
        content = content.encode("utf-8")
    return client.post(
        path,
        files={"file": (filename, content, "application/octet-stream")},
        data={k: str(v) for k, v in form.items() if v is not None},
    )


# ---------------------------------------------------------------------------
# §A.1 — the full-parity walk
# ---------------------------------------------------------------------------


class Walk:
    """The recorded result of one agent's trip through the API.

    Every response is kept so the assertions below can interrogate the
    envelope of each step without re-running the walk.
    """

    def __init__(self, agent: TestClient) -> None:
        self.agent = agent
        self.responses: dict[str, Any] = {}
        self.ids: dict[str, str] = {}

    def record(self, step: str, response, expected: int | tuple[int, ...] = (200, 201)):
        self.responses[step] = response
        return _envelope(response, expected)


@pytest.fixture
def owner(db) -> tuple[TestClient, str, str]:
    """A fresh workspace owner: their cookie client, workspace id, user id."""
    c = TestClient(app)
    data = signup_user(c, email=f"agent-{uuid.uuid4().hex[:8]}@x.com").json()["data"]
    return c, data["workspace_id"], data["user"]["id"]


@pytest.fixture
def walk(owner) -> Walk:
    """Create → wire EDA → stock → move → read, all under one full PAT.

    The payloads here are the ones `docs/api/agents.md` publishes as its
    quickstart. Keep the two in sync: the doc's promise is that a reader
    can paste them and get these results.
    """
    cookie_client, _ws_id, _user_id = owner
    agent = _agent_client(_mint(cookie_client)["token"])
    w = Walk(agent)

    category = w.record(
        "category",
        agent.post("/api/categories", json={"name": "Resistors", "refdes_prefix": "R"}),
        201,
    )
    w.ids["category"] = category["id"]

    part = w.record(
        "part",
        agent.post(
            "/api/parts",
            json={
                "name": "R-10k-0402",
                "part_type": "local",
                "mpn": f"RC0402-{uuid.uuid4().hex[:6]}",
                "category_id": category["id"],
                "low_stock_report_quantity": 100,
            },
        ),
    )
    w.ids["part"] = part["id"]

    symbol = w.record(
        "symbol",
        _upload(agent, "/api/eda/symbols", "R_10k.kicad_sym", SYMBOL_TEXT),
        201,
    )
    w.ids["symbol"] = symbol["id"]

    footprint = w.record(
        "footprint",
        _upload(agent, "/api/eda/footprints", "R_0402.kicad_mod", FOOTPRINT_TEXT),
        201,
    )
    w.ids["footprint"] = footprint["id"]

    datafile = w.record(
        "datafile",
        _upload(agent, "/api/eda/datafiles", "R_0402.step", STEP_BYTES),
        201,
    )
    w.ids["datafile"] = datafile["id"]

    w.record(
        "part_eda",
        agent.put(
            f"/api/parts/{part['id']}/eda",
            json={
                "symbol_id": symbol["id"],
                "footprint_id": footprint["id"],
                "value": "10k",
                "footprint_filters": ["R_0402*"],
            },
        ),
        200,
    )

    bin_a = w.record("storage_a", agent.post("/api/storage", json={"name": "Bin A"}), 201)
    bin_b = w.record("storage_b", agent.post("/api/storage", json={"name": "Bin B"}), 201)
    w.ids["storage_a"] = bin_a["id"]
    w.ids["storage_b"] = bin_b["id"]

    w.record(
        "stock_add",
        agent.post(
            "/api/stock/add",
            json={
                "part_id": part["id"],
                "quantity": 25,
                "storage_location_id": bin_a["id"],
            },
        ),
        200,
    )
    w.record("part_after_add", agent.get(f"/api/parts/{part['id']}"), 200)
    w.record(
        "stock_move",
        agent.post(
            "/api/stock/move",
            json={
                "part_id": part["id"],
                "source_storage_location_id": bin_a["id"],
                "destination_storage_location_id": bin_b["id"],
                "quantity": 10,
            },
        ),
        200,
    )
    w.record("storage_b_parts", agent.get(f"/api/storage/{bin_b['id']}/parts"), 200)
    w.record("search", agent.get("/api/search", params={"q": "R-10k-0402"}), 200)
    w.record("low_stock", agent.get("/api/reports/low-stock"), 200)
    w.record("part_eda_read", agent.get(f"/api/parts/{part['id']}/eda"), 200)
    return w


def test_every_walk_step_answers_the_envelope(walk):
    """Parity claim in its bluntest form: fifteen writes and reads, no
    cookie, no Origin, and not one of them deviates from `{data, status}`.

    The step names are asserted rather than counted so that deleting a
    step from the fixture fails here by name instead of silently shrinking
    what the rest of the module covers.
    """
    assert set(walk.responses) == {
        "category",
        "part",
        "symbol",
        "footprint",
        "datafile",
        "part_eda",
        "storage_a",
        "storage_b",
        "stock_add",
        "part_after_add",
        "stock_move",
        "storage_b_parts",
        "search",
        "low_stock",
        "part_eda_read",
    }
    for step, response in walk.responses.items():
        assert response.status_code in (200, 201), f"{step}: {response.text}"
        assert set(response.json()) >= {"data", "status"}, f"{step}: {response.text}"


def test_walk_client_sent_no_origin_header(walk):
    """Guards the technique, not the app.

    Every CSRF claim this module makes rests on the walk client having no
    `Origin`. If conftest's injection ever outruns `_agent_client`, the
    walk would still pass while proving nothing — so assert the absence
    directly.
    """
    assert "origin" not in walk.agent.headers
    assert "cookie" not in walk.agent.headers
    assert not walk.agent.cookies


def test_part_is_filed_under_the_token_created_category(walk):
    assert walk.responses["part"].json()["data"]["category_id"] == walk.ids["category"]


def test_multipart_uploads_land_under_header_auth(walk):
    """Symbol, footprint and STEP all arrive through `multipart/form-data`
    with no cookie — the lane most likely to be broken by an auth change,
    because it is the only one where the body is not JSON."""
    for step in ("symbol", "footprint", "datafile"):
        data = walk.responses[step].json()["data"]
        assert len(data["sha256"]) == 64, f"{step}: {data}"
        assert data["size_bytes"] > 0, f"{step}: {data}"
    assert walk.responses["datafile"].json()["data"]["kind"] == "step"


def test_part_eda_config_is_wired_to_the_uploaded_library(walk):
    config = walk.responses["part_eda_read"].json()["data"]
    assert config["symbol_id"] == walk.ids["symbol"]
    assert config["footprint_id"] == walk.ids["footprint"]
    assert config["value"] == "10k"


def test_ledger_quantity_reflects_the_token_authed_add(walk):
    part = walk.responses["part_after_add"].json()["data"]
    assert part["on_hand"] == 25
    assert part["available"] == 25


def test_move_relocates_the_stock_under_token_auth(walk):
    rows = walk.responses["storage_b_parts"].json()["data"]
    moved = [r for r in rows if r["part_id"] == walk.ids["part"]]
    assert [r["quantity"] for r in moved] == [10], rows


def test_search_finds_the_token_created_part(walk):
    found = walk.responses["search"].json()["data"]["parts"]
    assert walk.ids["part"] in [p["id"] for p in found]


def test_low_stock_report_lists_the_token_created_part(walk):
    rows = walk.responses["low_stock"].json()["data"]
    row = next(r for r in rows if r["part_id"] == walk.ids["part"])
    assert row["on_hand"] == 25
    assert row["threshold"] == 100
    assert row["short_by"] == 75


def test_audit_rows_are_attributed_to_the_token_owner(walk, owner, db):
    """A token acts *as* its owner, so the trail must name the human — not
    the token, and not nobody. An agent whose writes landed anonymously
    would be the worst possible outcome of this whole feature."""
    _client, ws_id, user_id = owner
    rows = list(
        db.execute(
            select(AuditLog).where(AuditLog.workspace_id == uuid.UUID(ws_id))
        ).scalars()
    )
    assert rows, "the walk wrote no audit rows at all"
    assert {str(r.user_id) for r in rows} == {user_id}

    actions = {r.action for r in rows}
    assert {
        "category.created",
        "part.created",
        "eda_symbol.uploaded",
        "eda_footprint.uploaded",
        "eda_datafile.uploaded",
        "part_eda.updated",
        "storage.created",
    } <= actions, sorted(actions)


def test_ledger_rows_are_attributed_to_the_token_owner(walk, owner, db):
    """Stock mutations write no audit row (the ledger *is* the trail), so
    their attribution has to be checked on the entries themselves."""
    _client, ws_id, user_id = owner
    entries = list(
        db.execute(
            select(StockEntry).where(StockEntry.workspace_id == uuid.UUID(ws_id))
        ).scalars()
    )
    assert entries
    assert {str(e.created_by) for e in entries} == {user_id}


# ---------------------------------------------------------------------------
# §A.1 (cont.) — role still decides, whichever credential carries it
# ---------------------------------------------------------------------------


@pytest.fixture
def member_token(owner) -> str:
    """A `member`-role teammate's token, pinned to the owner's workspace."""
    cookie_client, ws_id, _user_id = owner
    email = f"member-{uuid.uuid4().hex[:8]}@x.com"
    invite = cookie_client.post("/api/invitations", json={"email": email, "role": "member"})
    assert invite.status_code in (200, 201), invite.text

    joiner = TestClient(app)
    signup_user(joiner, email=email)
    accepted = joiner.post(
        "/api/invitations/accept", json={"token": invite.json()["data"]["token"]}
    )
    assert accepted.status_code == 200, accepted.text

    minted = joiner.post(
        "/api/tokens",
        json={"label": "member agent"},
        headers={"X-Workspace-Id": ws_id},
    )
    assert minted.status_code == 201, minted.text
    return minted.json()["data"]["token"]


def test_member_token_is_refused_the_admin_only_audit_list(member_token):
    r = _agent_client(member_token).get("/api/audit")
    assert r.status_code == 403, r.text
    assert _code(r) == "resource.insufficient_role"


def test_owner_token_reads_the_audit_list(walk, owner):
    """The same route, the same credential *kind*, a higher role — 200.
    Pins that the 403 above is the role check and not a token check."""
    r = walk.agent.get("/api/audit")
    assert r.status_code == 200, r.text
    assert r.json()["data"]


def test_member_token_can_still_write_the_ordinary_surface(member_token, walk):
    """A role floor on one route must not be mistaken for a token floor:
    the member's token writes stock against the owner-created part."""
    r = _agent_client(member_token).post(
        "/api/stock/add", json={"part_id": walk.ids["part"], "quantity": 5}
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# §A.1 (cont.) — the routers the walk does not visit
# ---------------------------------------------------------------------------

# Every parameterless `GET` under `/api` that a token is allowed to reach.
# The walk exercises nine of these in depth; the point of sweeping the rest
# is that a router which silently depends on the session cookie shows up
# here without anyone having to write it a walk of its own.
#
# Deliberately absent:
#   * `/api/tokens` — session-only, covered by §A.4.
#   * `/api/health` — no auth at all.
#   * `/api/search` — needs `q`, covered in the walk.
#   * `/api/reports/sourcing-risk`, `/api/reports/bom-buyability` —
#     reach the outbound provider lane, which has its own test modules and
#     its own network stubs. Auth is not what those would be testing.
#   * `/api/reports/bom-shortage` — needs a `project_id`; the project
#     surface is covered by `test_project_bom_and_build_write_...` below.
SWEEP_PATHS = [
    "/api/audit",
    "/api/auth/me",
    "/api/bom-presets",
    "/api/builds",
    "/api/categories",
    "/api/eda/datafiles",
    "/api/eda/footprints",
    "/api/eda/kicad-setup",
    "/api/eda/symbols",
    "/api/invitations",
    "/api/lots",
    "/api/orders",
    "/api/parts",
    "/api/projects",
    "/api/reports/expiring-lots",
    "/api/reports/low-stock",
    "/api/reports/replenishment-cost",
    "/api/reports/stock-value",
    "/api/sourcing/alerts",
    "/api/stock/history",
    "/api/storage",
    "/api/tags",
    "/api/workspaces",
    "/api/workspaces/current",
    "/api/workspaces/current/catalog/tokens",
    "/api/workspaces/current/scanner-license-key",
    "/api/workspaces/master-lists",
    "/api/workspaces/members",
]


@pytest.mark.parametrize("path", SWEEP_PATHS)
def test_every_read_surface_answers_a_token(walk, path):
    r = walk.agent.get(path)
    assert r.status_code == 200, f"{path}: {r.text}"
    assert set(r.json()) >= {"data", "status"}, f"{path}: {r.text}"


def test_workspace_spanning_reads_show_only_the_pinned_tenant(walk, owner):
    """`/api/auth/me` and `GET /api/workspaces` are the two reads that take
    only `CurrentUser`, so the workspace pinning in `get_current_workspace`
    never runs for them. Swept above for status; narrowed here for content,
    because a sweep that only checks `200` would miss a token enumerating
    its owner's other tenants."""
    _client, ws_id, _user_id = owner
    me = walk.agent.get("/api/auth/me").json()["data"]
    assert [w["id"] for w in me["workspaces"]] == [ws_id]
    assert [w["id"] for w in walk.agent.get("/api/workspaces").json()["data"]] == [ws_id]


def test_project_bom_and_build_write_under_token_auth(walk):
    """Projects, BOM entries, builds and orders are the four write surfaces
    the walk skips. One row each is enough — what is under test is the
    credential, not the domain logic each of those has its own module for."""
    agent = walk.agent
    project = _envelope(agent.post("/api/projects", json={"name": "Agent board"}), 201)
    entry = _envelope(
        agent.post(
            f"/api/projects/{project['id']}/entries",
            json={"part_id": walk.ids["part"], "quantity": 4, "dnp": False},
        )
    )
    assert entry["part_id"] == walk.ids["part"]

    build = _envelope(
        agent.post(
            "/api/builds",
            json={"name": "Proto run", "project_id": project["id"], "quantity": 2},
        ),
        201,
    )
    assert build["project_id"] == project["id"]

    order = _envelope(
        agent.post("/api/orders", json={"name": "PO-1", "order_type": "purchase"}), 201
    )
    assert order["name"] == "PO-1"

    shortage = _envelope(
        agent.get("/api/reports/bom-shortage", params={"project_id": project["id"]}), 200
    )
    assert shortage["project_id"] == project["id"]


# ---------------------------------------------------------------------------
# §A.2 — read_only tokens: every read, no writes
# ---------------------------------------------------------------------------


@pytest.fixture
def read_only_walk(owner, walk) -> tuple[TestClient, Walk]:
    """A read-only token over the state the full-token walk built."""
    cookie_client, _ws_id, _user_id = owner
    token = _mint(cookie_client, label="read only agent", read_only=True)["token"]
    return _agent_client(token), walk


# Paths carry `{name}` placeholders filled from `Walk.ids`, so the
# parametrisation can be a module-level constant (readable test ids) while
# still pointing at rows the walk actually created.
READ_PATHS = [
    "/api/categories",
    "/api/parts",
    "/api/parts/{part}",
    "/api/parts/{part}/eda",
    "/api/eda/symbols",
    "/api/eda/footprints",
    "/api/eda/datafiles",
    "/api/storage",
    "/api/storage/{storage_b}/parts",
    "/api/stock/history",
    "/api/reports/low-stock",
    "/api/audit",
    "/api/search?q=R-10k-0402",
]


@pytest.mark.parametrize("path", READ_PATHS)
def test_read_only_token_reads_every_surface(read_only_walk, path):
    agent, w = read_only_walk
    r = agent.get(path.format(**w.ids))
    assert r.status_code == 200, f"{path}: {r.text}"
    assert set(r.json()) >= {"data", "status"}, f"{path}: {r.text}"


# One write per area the walk exercised, in the same shapes.
WRITE_PROBES: list[tuple[str, str, dict | None]] = [
    ("post", "/api/categories", {"name": "Capacitors"}),
    ("post", "/api/parts", {"name": "second", "part_type": "local"}),
    ("patch", "/api/parts/{part}", {"description": "edited"}),
    ("put", "/api/parts/{part}/eda", {"value": "22k"}),
    ("delete", "/api/parts/{part}/eda", None),
    ("post", "/api/storage", {"name": "Bin C"}),
    ("post", "/api/stock/add", {"part_id": "{part}", "quantity": 1}),
    (
        "post",
        "/api/stock/move",
        {
            "part_id": "{part}",
            "source_storage_location_id": "{storage_b}",
            "destination_storage_location_id": "{storage_a}",
            "quantity": 1,
        },
    ),
    ("post", "/api/categories/{category}/archive", None),
]


@pytest.mark.parametrize(("method", "path", "body"), WRITE_PROBES)
def test_read_only_token_refuses_every_write(read_only_walk, method, path, body):
    agent, w = read_only_walk
    kwargs = {}
    if body is not None:
        kwargs["json"] = {
            k: (v.format(**w.ids) if isinstance(v, str) else v) for k, v in body.items()
        }
    r = getattr(agent, method)(path.format(**w.ids), **kwargs)
    assert r.status_code == 403, f"{path}: {r.text}"
    assert _code(r) == "auth.token_read_only", f"{path}: {r.text}"


def test_read_only_token_refuses_multipart_uploads(read_only_walk):
    """The upload lane reads the body before the route runs, so it is the
    one place a `read_only` check could plausibly arrive too late."""
    agent, _w = read_only_walk
    r = _upload(agent, "/api/eda/symbols", "R_10k.kicad_sym", SYMBOL_TEXT)
    assert r.status_code == 403, r.text
    assert _code(r) == "auth.token_read_only"


def test_read_only_refusal_precedes_the_route(read_only_walk):
    """A write to an id that does not exist is still 403, never 404 — the
    refusal happens in `deps.py` before any handler sees the request, so a
    read-only token cannot probe for existence by writing."""
    agent, _w = read_only_walk
    r = agent.patch(f"/api/parts/{uuid.uuid4()}", json={"description": "x"})
    assert r.status_code == 403, r.text
    assert _code(r) == "auth.token_read_only"


def test_read_only_writes_left_the_data_untouched(read_only_walk):
    agent, w = read_only_walk
    part = agent.get(f"/api/parts/{w.ids['part']}").json()["data"]
    assert part["on_hand"] == 25
    assert part["description"] != "edited"


# ---------------------------------------------------------------------------
# §A.3 — CSRF: the contrast case
# ---------------------------------------------------------------------------


def test_the_same_write_with_a_cookie_and_no_origin_is_refused(owner, walk):
    """The walk's Origin-free writes are only interesting if the *cookie*
    path still refuses them. Same request, same server, session credential
    instead of a token: 403 from `CsrfOriginMiddleware`."""
    cookie_client, _ws_id, _user_id = owner
    cookie_client.headers.pop("origin", None)
    r = cookie_client.post("/api/categories", json={"name": "Inductors"})
    assert r.status_code == 403, r.text


def test_multipart_write_without_origin_is_accepted_under_token_auth(walk):
    """Re-asserted outside the walk fixture so the multipart + no-Origin
    combination is pinned by a test that names it."""
    r = _upload(
        walk.agent, "/api/eda/footprints", "R_0603.kicad_mod",
        FOOTPRINT_TEXT.replace("R_0402", "R_0603"),
    )
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# §A.4 — blocked surfaces, enumerated from the code
# ---------------------------------------------------------------------------


def _dependency_calls(dependant):
    yield dependant.call
    for sub in dependant.dependencies:
        yield from _dependency_calls(sub)


def _blocked_routes() -> list[tuple[str, str]]:
    """Every (METHOD, path) the app guards with `forbid_api_token`.

    Read out of the live dependency graph rather than hand-listed, so this
    module cannot fall behind `deps.py`. Router-level dependencies land in
    each route's dependant tree, which is why `/api/tokens` — guarded on
    its `APIRouter` — shows up here per-route.
    """
    out: list[tuple[str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if forbid_api_token not in set(_dependency_calls(route.dependant)):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            out.append((method, route.path))
    return sorted(out)


# The session-only surface as of this commit. Adding a route to
# `forbid_api_token` is a deliberate act — widening what a browser-only
# credential is required for — so it has to be written down here too, and
# it should come with a line in `docs/api/agents.md` and `docs/api/tokens.md`.
EXPECTED_BLOCKED = sorted(
    [
        ("GET", "/api/tokens"),
        ("POST", "/api/tokens"),
        ("POST", "/api/tokens/{token_id}/revoke"),
        ("POST", "/api/workspaces"),
        ("PATCH", "/api/workspaces/current"),
        ("POST", "/api/workspaces/current/catalog/tokens"),
        ("DELETE", "/api/workspaces/current/catalog/tokens/{token_id}"),
        ("PATCH", "/api/workspaces/members/{member_id}"),
        ("DELETE", "/api/workspaces/members/{member_id}"),
        ("POST", "/api/workspaces/{workspace_id}/switch"),
        ("POST", "/api/invitations"),
        ("DELETE", "/api/invitations/{invitation_id}"),
        ("POST", "/api/invitations/accept"),
    ]
)

# Bodies that would be *valid* if the guard were not there, so a 403 can
# only be the guard — never a 422 that happens to share the status.
_BLOCKED_BODIES: dict[tuple[str, str], dict] = {
    ("POST", "/api/workspaces"): {"name": "smuggled org"},
    ("PATCH", "/api/workspaces/current"): {"name": "renamed by a token"},
    ("POST", "/api/workspaces/current/catalog/tokens"): {"label": "smuggled"},
    ("PATCH", "/api/workspaces/members/{member_id}"): {"role": "admin"},
    ("POST", "/api/invitations"): {"email": "accomplice@x.com", "role": "admin"},
    ("POST", "/api/invitations/accept"): {"token": "smk_deadbeef.nope"},
    ("POST", "/api/tokens"): {"label": "successor"},
}


def test_the_blocked_surface_list_matches_the_code():
    """Fails in both directions: a new guarded route nobody probed, and a
    route that quietly lost its guard. The second is the dangerous one —
    it would hand every leaked token the ability to mint a successor."""
    assert _blocked_routes() == EXPECTED_BLOCKED


@pytest.mark.parametrize(("method", "path"), EXPECTED_BLOCKED)
def test_blocked_surface_refuses_a_token_with_the_one_code(walk, method, path):
    """One code for the whole class — an agent switches on
    `auth.token_no_token_management` and knows to tell its human to open a
    browser, without having to learn thirteen special cases.

    The walk's token belongs to the workspace *owner*, so the role checks
    that sit alongside the guard on several of these routes all pass; what
    is left refusing the request is the guard itself.
    """
    concrete = path
    while "{" in concrete:
        head, _, rest = concrete.partition("{")
        _param, _, tail = rest.partition("}")
        concrete = f"{head}{uuid.uuid4()}{tail}"

    kwargs: dict[str, Any] = {}
    body = _BLOCKED_BODIES.get((method, path))
    if body is not None:
        kwargs["json"] = body
    r = walk.agent.request(method, concrete, **kwargs)

    assert r.status_code == 403, f"{method} {concrete}: {r.text}"
    assert _code(r) == "auth.token_no_token_management", f"{method} {concrete}: {r.text}"


def test_blocked_surfaces_are_reachable_with_the_session_cookie(owner):
    """The 403s above have to be the guard, not a permanently broken route.
    The two read-shaped members of the list answer normally for a cookie."""
    cookie_client, _ws_id, _user_id = owner
    assert cookie_client.get("/api/tokens").status_code == 200
    assert cookie_client.get("/api/workspaces/members").status_code == 200


# ---------------------------------------------------------------------------
# §A.5 — multipart on the polymorphic attachment lane
# ---------------------------------------------------------------------------


def test_attachment_upload_works_under_token_auth(walk):
    """`/api/attachments` is a second, independent multipart lane — it
    validates magic bytes and takes its object reference from form fields
    rather than the path, so it can break while the EDA uploads still work."""
    r = walk.agent.post(
        "/api/attachments",
        files={"file": ("datasheet.png", PNG_BYTES, "image/png")},
        data={"object_type": "part", "object_id": walk.ids["part"], "file_type": "other"},
    )
    data = _envelope(r, 201)
    assert data["mime_type"] == "image/png"
    assert data["file_name"].endswith(".png")

    listed = _envelope(walk.agent.get(f"/api/attachments/by-object/part/{walk.ids['part']}"), 200)
    assert [a["id"] for a in listed] == [data["id"]]


# ---------------------------------------------------------------------------
# §C — the OpenAPI affordance
# ---------------------------------------------------------------------------


def test_openapi_advertises_the_token_scheme(client):
    """An agent pointed at `/openapi.json` cold has to be able to learn that
    the API takes a header token; FastAPI cannot infer that, because the
    credential is read out of `request.headers` rather than declared as a
    security dependency."""
    schema = client.get("/openapi.json").json()
    scheme = schema["components"]["securitySchemes"]["ApiToken"]
    assert scheme["type"] == "http"
    assert scheme["scheme"] == "bearer"
    assert "SessionCookie" in schema["components"]["securitySchemes"]


def test_openapi_declares_credentials_as_optional(client):
    """The empty requirement object. Without it the app-wide declaration
    would claim `/api/auth/login` and `/api/health` need a token."""
    assert {} in client.get("/openapi.json").json()["security"]


def test_openapi_declaration_changed_no_runtime_auth(client):
    """The whole affordance is documentation. Nothing about who gets in may
    have moved: no credential is still 401, a junk token is still 401, and
    the code is the one `deps.py` picks — not something a security
    dependency injected."""
    assert client.get("/api/parts").status_code == 401
    r = client.get("/api/parts", headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401, r.text
    assert _code(r) == "auth.invalid_token"


def test_attachment_upload_is_refused_for_a_read_only_token(read_only_walk):
    agent, w = read_only_walk
    r = agent.post(
        "/api/attachments",
        files={"file": ("datasheet.png", PNG_BYTES, "image/png")},
        data={"object_type": "part", "object_id": w.ids["part"]},
    )
    assert r.status_code == 403, r.text
    assert _code(r) == "auth.token_read_only"
