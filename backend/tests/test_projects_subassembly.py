"""Tests for associated_subassembly_part_id on projects (BE-008).

Covers: set via create, set via patch, replace, clear, cross-workspace
isolation, archived-part rejection, and the end-to-end consume path that
produces an output lot.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests._factories import (
    add_stock as _add_stock,
    create_part as _create_part,
    create_project_with_bom as _create_project_with_bom,
    create_storage as _create_storage,
    signup_user,
)


@pytest.fixture
def authed():
    c = TestClient(app)
    signup_user(c)
    return c


@pytest.fixture
def other():
    """A second, independent workspace client."""
    c = TestClient(app)
    signup_user(c)
    return c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_project(client: TestClient, name: str = "Proj") -> str:
    r = client.post("/api/projects", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _get_project(client: TestClient, pid: str) -> dict:
    r = client.get(f"/api/projects/{pid}")
    assert r.status_code == 200, r.text
    return r.json()["data"]


# ---------------------------------------------------------------------------
# test_create_project_with_subassembly
# ---------------------------------------------------------------------------

def test_create_project_with_subassembly(authed):
    c = authed
    sub = _create_part(c, "SUB-A")

    r = c.post(
        "/api/projects",
        json={"name": "PCB-SUB", "associated_subassembly_part_id": sub},
    )
    assert r.status_code in (200, 201), r.text
    data = r.json()["data"]
    assert data["associated_subassembly_part_id"] == sub


# ---------------------------------------------------------------------------
# test_patch_project_set_subassembly
# ---------------------------------------------------------------------------

def test_patch_project_set_subassembly(authed):
    c = authed
    sub = _create_part(c, "SUB-B")
    pid = _create_project(c, "PCB-SET")

    r = c.patch(f"/api/projects/{pid}", json={"associated_subassembly_part_id": sub})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["associated_subassembly_part_id"] == sub

    # Persisted
    assert _get_project(c, pid)["associated_subassembly_part_id"] == sub


# ---------------------------------------------------------------------------
# test_patch_project_replace_subassembly
# ---------------------------------------------------------------------------

def test_patch_project_replace_subassembly(authed):
    c = authed
    sub1 = _create_part(c, "SUB-C1")
    sub2 = _create_part(c, "SUB-C2")
    pid = _create_project(c, "PCB-REP")

    r = c.patch(f"/api/projects/{pid}", json={"associated_subassembly_part_id": sub1})
    assert r.status_code == 200
    assert r.json()["data"]["associated_subassembly_part_id"] == sub1

    r = c.patch(f"/api/projects/{pid}", json={"associated_subassembly_part_id": sub2})
    assert r.status_code == 200
    assert r.json()["data"]["associated_subassembly_part_id"] == sub2

    assert _get_project(c, pid)["associated_subassembly_part_id"] == sub2


# ---------------------------------------------------------------------------
# test_patch_project_clear_subassembly
# ---------------------------------------------------------------------------

def test_patch_project_clear_subassembly(authed):
    c = authed
    sub = _create_part(c, "SUB-D")
    pid = _create_project(c, "PCB-CLR")

    r = c.patch(f"/api/projects/{pid}", json={"associated_subassembly_part_id": sub})
    assert r.status_code == 200

    # Explicitly null — must clear without calling _assert_part_live
    r = c.patch(f"/api/projects/{pid}", json={"associated_subassembly_part_id": None})
    assert r.status_code == 200
    assert r.json()["data"]["associated_subassembly_part_id"] is None

    assert _get_project(c, pid)["associated_subassembly_part_id"] is None


# ---------------------------------------------------------------------------
# test_create_project_rejects_foreign_workspace_part
# ---------------------------------------------------------------------------

def test_create_project_rejects_foreign_workspace_part(authed, other):
    c = authed
    # Create a part in a different workspace
    foreign_part = _create_part(other, "FOREIGN")

    r = c.post(
        "/api/projects",
        json={"name": "PCB-FW", "associated_subassembly_part_id": foreign_part},
    )
    # Must be 404 — the existence oracle invariant (not 403)
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# test_create_project_rejects_archived_part
# ---------------------------------------------------------------------------

def test_create_project_rejects_archived_part(authed):
    c = authed
    sub = _create_part(c, "SUB-ARC")

    # Archive the part
    r = c.post(f"/api/parts/{sub}/archive")
    assert r.status_code == 200

    r = c.post(
        "/api/projects",
        json={"name": "PCB-ARC", "associated_subassembly_part_id": sub},
    )
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# test_patch_project_rejects_archived_part_bind
# ---------------------------------------------------------------------------

def test_patch_project_rejects_archived_part_bind(authed):
    c = authed
    sub = _create_part(c, "SUB-PARC")
    pid = _create_project(c, "PCB-PARC")

    # Archive the part
    r = c.post(f"/api/parts/{sub}/archive")
    assert r.status_code == 200

    r = c.patch(f"/api/projects/{pid}", json={"associated_subassembly_part_id": sub})
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# test_consume_with_subassembly_produces_output_lot
# ---------------------------------------------------------------------------

def test_consume_with_subassembly_produces_output_lot(authed):
    """End-to-end: a consumed build with an associated sub-assembly part
    must produce a `build_produce` stock entry for that part."""
    c = authed
    sub = _create_part(c, "SubAsm-E2E")
    p1 = _create_part(c, "R1k-E2E")
    storage = _create_storage(c)
    _add_stock(c, p1, 200, storage)

    proj_id = _create_project_with_bom(c, "PCB-E2E", [{"part_id": p1, "quantity": 10}])

    # Bind the sub-assembly part via PATCH
    r = c.patch(f"/api/projects/{proj_id}", json={"associated_subassembly_part_id": sub})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["associated_subassembly_part_id"] == sub

    # Create and consume the build (qty=5 → 10*5=50 of p1 consumed)
    r = c.post("/api/builds", json={"name": "B-E2E", "project_id": proj_id, "quantity": 5})
    assert r.status_code == 201, r.text
    bid = r.json()["data"]["id"]

    entries = c.get(f"/api/projects/{proj_id}/entries").json()["data"]
    e1 = next(e for e in entries if e["part_id"] == p1)

    r = c.post(
        f"/api/builds/{bid}/consume",
        json={
            "lines": [
                {
                    "project_entry_id": e1["id"],
                    "part_id": p1,
                    "quantity": 50,
                    "storage_location_id": storage,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "complete"

    # p1 stock should be 200 - 50 = 150
    s1 = c.get(f"/api/parts/{p1}/stock").json()["data"]["total_on_hand"]
    assert s1 == 150

    # Sub-assembly part should now have 5 units produced
    sub_stock = c.get(f"/api/parts/{sub}/stock").json()["data"]["total_on_hand"]
    assert sub_stock == 5
