"""SM-004 REST parity — `lifecycle` / `compliance` on the parts REST routes.

netsch reads parts over REST (`GET /api/parts?mpn=` then
`GET /api/parts/{id}`), so REST must carry the same objects MCP `get_part`
returns. One helper (`lifecycle_compliance.for_parts`) feeds both, and these
tests pin the equality for the three cases that matter: a DigiKey-answered
part (with a Mouser alternate), a Mouser-only part (`provider_silent`) and
an unlinked part (`never_fetched`).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from tests._factories import signup_user
from tests.test_lifecycle_compliance import (
    _MOUSER_SILENT,
    MPN,
    _get_part,
    _mouser_only_part,
    _refreshed_part,
)
from tests.test_secondary_provider import _create_part


def _authed() -> TestClient:
    c = TestClient(app)
    signup_user(c)
    return c


def _rest_part(c: TestClient, part_id: str) -> dict:
    r = c.get(f"/api/parts/{part_id}")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _rest_list(c: TestClient, **params) -> dict[str, dict]:
    r = c.get("/api/parts", params=params)
    assert r.status_code == 200, r.text
    return {row["id"]: row for row in r.json()["data"]}


async def _assert_rest_matches_mcp(c: TestClient, part_id: str) -> dict:
    mcp = await _get_part(c, part_id)
    rest = _rest_part(c, part_id)
    assert rest["lifecycle"] == mcp["lifecycle"]
    assert rest["compliance"] == mcp["compliance"]
    listed = _rest_list(c)[part_id]
    assert listed["lifecycle"] == mcp["lifecycle"]
    assert listed["compliance"] == mcp["compliance"]
    return rest


async def test_rest_equals_mcp_for_a_digikey_part_with_a_mouser_alternate(monkeypatch):
    c = _authed()
    part_id = _refreshed_part(c, monkeypatch)
    rest = await _assert_rest_matches_mcp(c, part_id)
    assert rest["lifecycle"]["status"] == "active"
    assert rest["lifecycle"]["source"] == "digikey"
    assert rest["lifecycle"]["alternates"][0]["status"] == "obsolete"
    assert rest["compliance"]["rohs"] == "ROHS3 Compliant"


async def test_rest_equals_mcp_for_a_mouser_only_part_provider_silent(monkeypatch):
    c = _authed()
    part_id = _mouser_only_part(c, monkeypatch, _MOUSER_SILENT)
    rest = await _assert_rest_matches_mcp(c, part_id)
    assert rest["lifecycle"]["status"] == "unknown"
    assert rest["lifecycle"]["reason"] == "provider_silent"


async def test_rest_equals_mcp_for_an_unlinked_part_never_fetched():
    c = _authed()
    part_id = _create_part(c, name="Bare", mpn=None)
    rest = await _assert_rest_matches_mcp(c, part_id)
    assert rest["lifecycle"]["reason"] == "never_fetched"
    assert rest["compliance"]["reason"] == "never_fetched"


def test_mpn_lookup_carries_lifecycle_so_no_second_request_is_needed(monkeypatch):
    """netsch's `find_parts(mpn=…)` path: the hit already has lifecycle."""
    c = _authed()
    part_id = _refreshed_part(c, monkeypatch, mouser=False)
    hits = _rest_list(c, mpn=MPN, limit=2)
    assert list(hits) == [part_id]
    assert hits[part_id]["lifecycle"]["status"] == "active"

    paged = c.get("/api/parts", params={"paged": "true"})
    assert paged.status_code == 200, paged.text
    [row] = paged.json()["data"]["items"]
    assert row["lifecycle"] == hits[part_id]["lifecycle"]


def test_lifecycle_never_reads_another_workspaces_rows(monkeypatch):
    """Same MPN in two workspaces: B's part was never fetched, and must not
    pick up the rows A's refresh stored."""
    a = _authed()
    _refreshed_part(a, monkeypatch)
    b = _authed()
    b_part = _create_part(b, name="P-MOSFET", mpn=MPN)

    assert _rest_part(b, b_part)["lifecycle"]["reason"] == "never_fetched"
    assert _rest_list(b, mpn=MPN)[b_part]["compliance"]["source"] == "unknown"


def test_create_part_echo_omits_lifecycle():
    """Create-part does not load the rows, so the keys are absent (not a
    fabricated `unknown`) — same rule as `provider_links`."""
    c = _authed()
    r = c.post("/api/parts", json={"name": "Echo"})
    assert r.status_code == 201, r.text
    body = r.json()["data"]
    assert "lifecycle" not in body and "compliance" not in body
