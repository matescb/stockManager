"""The part-policy MCP tools: `get_part_policy`, `validate_part`,
`part_policy_status`, and the policy check inside `create_part`.

The REST contract is pinned in `test_part_policy.py`; this file pins the
MCP half, which differs in one deliberate way: a policy refusal from
`create_part` is a structured RESULT (`created: false`, `missing`), not
a tool error, because an agent has to act on the list field by field.
Everything else must read the same as REST — same items, same rule ids.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.parts.models import Part
from app.main import app
from tests._factories import create_part, signup_user
from tests.test_mcp import _mint, call, call_error, mcp_session


def _set_policy(client: TestClient, rules: list[dict]) -> None:
    r = client.put("/api/part-policy", json={"rules": rules})
    assert r.status_code == 200, r.text


def _category(client: TestClient, name: str, **extra) -> dict:
    r = client.post("/api/categories", json={"name": name, **extra})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]


async def test_get_part_policy_matches_rest(authed_client):
    _set_policy(authed_client, [{"id": "mfr", "field": "manufacturer", "when": "create"}])
    token = _mint(authed_client, read_only=True)["token"]
    async with mcp_session(token) as s:
        out = await call(s, "get_part_policy")
    assert out == authed_client.get("/api/part-policy").json()["data"]


async def test_validate_part_is_a_dry_run(authed_client, db):
    cat = _category(authed_client, "Resistors")
    _set_policy(
        authed_client,
        [
            {"id": "mfr", "field": "manufacturer", "when": "create", "hint": "from the datasheet"},
            {"id": "cat", "field": "category_id", "when": "create"},
            {"id": "ds", "field": "datasheet"},
        ],
    )
    # Read-only token: validate is a read tool.
    token = _mint(authed_client, read_only=True)["token"]
    async with mcp_session(token) as s:
        out = await call(s, "validate_part", mpn="MCP-V-1")
        assert out["ok"] is False
        assert [m["rule_id"] for m in out["missing"]] == ["mfr", "cat", "ds"]
        assert out["missing"][0]["hint"] == "from the datasheet"
        expected = {"id": cat["id"], "name": "Resistors", "path": "Resistors"}
        assert expected in out["missing"][1]["allowed"]

        out = await call(
            s, "validate_part", mpn="MCP-V-1", manufacturer="Yageo", category_id="Resistors"
        )
        assert out["ok"] is True
        assert [m["rule_id"] for m in out["missing"]] == ["ds"]
    assert db.query(Part).filter(Part.mpn == "MCP-V-1").count() == 0


async def test_create_part_returns_missing_on_policy_violation(authed_client, db):
    _set_policy(
        authed_client,
        [
            {"id": "mfr", "field": "manufacturer", "when": "create"},
            {"id": "fp", "field": "footprint", "when": "create"},
            {"id": "sym", "field": "eda.symbol"},
        ],
    )
    token = _mint(authed_client)["token"]
    async with mcp_session(token) as s:
        out = await call(s, "create_part", mpn="MCP-C-1")
        assert out["created"] is False
        assert out["found_existing"] is False
        assert out["error"] == "part.policy_violation"
        assert [m["rule_id"] for m in out["missing"]] == ["mfr", "fp"]
        assert "part" not in out
        assert db.query(Part).filter(Part.mpn == "MCP-C-1").count() == 0

        out = await call(
            s, "create_part", mpn="MCP-C-1", manufacturer="TI", footprint="SOIC-8"
        )
        assert out["created"] is True
        assert out["part"]["mpn"] == "MCP-C-1"
        # Complete rules still outstanding, as policy-status reports them.
        assert [m["rule_id"] for m in out["incomplete"]] == ["sym"]

        status = await call(s, "part_policy_status", part_id_or_mpn="MCP-C-1")
        assert status["complete"] is False
        assert [m["rule_id"] for m in status["missing"]] == ["sym"]


async def test_create_part_empty_policy_unchanged(authed_client):
    token = _mint(authed_client)["token"]
    async with mcp_session(token) as s:
        out = await call(s, "create_part", mpn="MCP-E-1")
    assert out["created"] is True
    assert out["found_existing"] is False
    assert out["incomplete"] == []


async def test_create_part_existing_mpn_still_found_under_policy(authed_client):
    create_part(authed_client, "Held", mpn="MCP-H-1")
    _set_policy(authed_client, [{"field": "manufacturer", "when": "create"}])
    token = _mint(authed_client)["token"]
    async with mcp_session(token) as s:
        out = await call(s, "create_part", mpn="MCP-H-1")
    assert out["found_existing"] is True
    assert out["created"] is False


async def test_create_part_read_only_token_refused_before_policy(authed_client, db):
    _set_policy(authed_client, [{"field": "manufacturer", "when": "create"}])
    token = _mint(authed_client, read_only=True)["token"]
    async with mcp_session(token) as s:
        error = await call_error(s, "create_part", mpn="MCP-RO-1")
    assert "auth.token_read_only" in error


async def test_part_policy_status_refuses_a_foreign_part(db):
    a, b = TestClient(app), TestClient(app)
    signup_user(a)
    signup_user(b)
    create_part(a, "A only", mpn="MCP-ISO-P-1")
    token_b = _mint(b)["token"]
    async with mcp_session(token_b) as s:
        error = await call_error(s, "part_policy_status", part_id_or_mpn="MCP-ISO-P-1")
    assert "part.not_found" in error


async def test_policy_is_per_workspace_over_mcp(db):
    a, b = TestClient(app), TestClient(app)
    signup_user(a)
    signup_user(b)
    _set_policy(a, [{"field": "manufacturer", "when": "create"}])
    token_b = _mint(b)["token"]
    async with mcp_session(token_b) as s:
        assert (await call(s, "get_part_policy"))["rules"] == []
        assert (await call(s, "validate_part", mpn="MCP-ISO-1"))["ok"] is True
        assert (await call(s, "create_part", mpn="MCP-ISO-1"))["created"] is True
