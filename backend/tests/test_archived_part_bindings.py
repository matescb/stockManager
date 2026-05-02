"""Refuse archived-part bindings on write paths (BE2-016).

Read endpoints (`GET /api/parts/{id}`, `/activity`, `/stock`) keep
returning the archived part so the user can review it. Write paths
(PATCH, substitutes add, BOM add/patch/match) refuse with 404 — same
404 as a non-existent id, since the operator's mental model treats both
as "this id is dead".
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient) -> str:
    r = c.post(
        "/api/auth/signup",
        json={
            "email": f"u-{uuid.uuid4().hex[:8]}@x.com",
            "name": "u",
            "password": "TestPass-2026-Stronk",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["workspace_id"]


@pytest.fixture
def authed():
    c = TestClient(app)
    _signup(c)
    return c


def _create_archived_part(c: TestClient, name: str = "Dead") -> str:
    pid = c.post("/api/parts", json={"name": name, "part_type": "local"}).json()["data"]["id"]
    r = c.post(f"/api/parts/{pid}/archive")
    assert r.status_code == 200, r.text
    return pid


def test_archived_part_get_still_returns_200(authed):
    """Read paths must keep working — operators inspect archived parts
    when they're considering restoration."""
    pid = _create_archived_part(authed)
    r = authed.get(f"/api/parts/{pid}")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["archived_at"] is not None


def test_archived_part_patch_refuses_with_404(authed):
    pid = _create_archived_part(authed)
    r = authed.patch(f"/api/parts/{pid}", json={"name": "renamed"})
    assert r.status_code == 404, r.text


def test_archived_part_substitute_add_refuses_with_404(authed):
    """Adding a substitute against an archived part — either side —
    must 404. Bad bindings would mislead BOM-shortage analysis."""
    live = authed.post("/api/parts", json={"name": "Live", "part_type": "local"}).json()["data"]["id"]
    dead = _create_archived_part(authed)

    # archived primary
    r = authed.post(
        f"/api/parts/{dead}/substitutes",
        json={"substitute_part_id": live},
    )
    assert r.status_code == 404, r.text

    # archived substitute target
    r = authed.post(
        f"/api/parts/{live}/substitutes",
        json={"substitute_part_id": dead},
    )
    assert r.status_code == 404, r.text


def test_archived_part_bom_add_refuses_with_404(authed):
    """Adding a BOM entry that points at an archived part must 404 —
    builds against this BOM would later raise on consume because the
    archived part has no available stock by definition."""
    proj = authed.post("/api/projects", json={"name": "P"}).json()["data"]["id"]
    dead = _create_archived_part(authed)

    r = authed.post(
        f"/api/projects/{proj}/entries",
        json={"entry_type": "part", "part_id": dead, "quantity": 1},
    )
    assert r.status_code == 404, r.text


def test_archived_part_bom_patch_refuses_with_404(authed):
    """The same vector via PATCH on an existing entry — patch_entry
    must mirror add_entry's archived-part guard."""
    proj = authed.post("/api/projects", json={"name": "P"}).json()["data"]["id"]
    dead = _create_archived_part(authed)
    entry = authed.post(
        f"/api/projects/{proj}/entries",
        json={"entry_type": "unmatched", "name": "x", "quantity": 1},
    ).json()["data"]["id"]

    r = authed.patch(f"/api/projects/{proj}/entries/{entry}", json={"part_id": dead})
    assert r.status_code == 404, r.text


def test_archived_part_bom_match_refuses_with_404(authed):
    """The /match endpoint must also refuse — same write-path family."""
    proj = authed.post("/api/projects", json={"name": "P"}).json()["data"]["id"]
    dead = _create_archived_part(authed)
    entry = authed.post(
        f"/api/projects/{proj}/entries",
        json={"entry_type": "unmatched", "name": "x", "quantity": 1},
    ).json()["data"]["id"]

    r = authed.post(
        f"/api/projects/{proj}/entries/{entry}/match",
        json={"part_id": dead},
    )
    assert r.status_code == 404, r.text


def test_archived_part_stock_summary_still_returns_200(authed):
    """Stock summary read on an archived part keeps working — auditing
    a wound-down part for residual stock is the whole point."""
    pid = _create_archived_part(authed)
    r = authed.get(f"/api/parts/{pid}/stock")
    assert r.status_code == 200, r.text
