"""Track B1 — per-BOM-line attrition (waste rate).

A per-`project_entries` waste percentage inflates the quantity a build
requires and consumes. Because stock is an integer-only ledger, the
attrition-adjusted requirement is rounded UP before it drives shortage
analysis, reservations, and consumption — planning and consumption must
agree on the same integer.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests._factories import add_stock as _add_stock
from tests._factories import create_part as _create_part
from tests._factories import create_storage as _create_storage
from tests._factories import signup_user


@pytest.fixture
def authed():
    c = TestClient(app)
    signup_user(c)
    return c


def _project(c: TestClient, name: str) -> str:
    r = c.post("/api/projects", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _add_entry(c: TestClient, project_id: str, **body) -> dict:
    r = c.post(f"/api/projects/{project_id}/entries", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]


def _build(c: TestClient, project_id: str, quantity: int) -> str:
    r = c.post("/api/builds", json={"name": "B", "project_id": project_id, "quantity": quantity})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


# --- Math: shortage_analysis ceil-rounds the line attrition -------------------


def test_shortage_required_ceil_rounds_line_attrition(authed):
    """100 base × 1 build × 2.5% waste = 102.5 → rounds UP to 103, not 102."""
    c = authed
    part = _create_part(c, "R1k 0402")
    storage = _create_storage(c)
    _add_stock(c, part, 100, storage)

    project_id = _project(c, "PCB-ATTR")
    _add_entry(c, project_id, part_id=part, quantity=100, attrition_pct=2.5)

    bid = _build(c, project_id, quantity=1)
    shortage = c.get(f"/api/builds/{bid}").json()["data"]["shortage"]
    row = next(r for r in shortage if r["part_id"] == part)
    assert row["required"] == 103, row
    assert row["attrition_pct"] == 2.5
    # 100 on hand, need 103 → short by 3
    assert row["short_by"] == 3


def test_shortage_required_ceil_rounds_fractional_up(authed):
    """7 base × 1 build × 10% = 7.7 → 8 (any fractional remainder rounds up)."""
    c = authed
    part = _create_part(c, "C100n")
    storage = _create_storage(c)
    _add_stock(c, part, 100, storage)

    project_id = _project(c, "PCB-FRAC")
    _add_entry(c, project_id, part_id=part, quantity=7, attrition_pct=10)

    bid = _build(c, project_id, quantity=1)
    shortage = c.get(f"/api/builds/{bid}").json()["data"]["shortage"]
    row = next(r for r in shortage if r["part_id"] == part)
    assert row["required"] == 8, row


def test_zero_attrition_leaves_base_required(authed):
    """Default attrition_pct=0 must not change the base requirement."""
    c = authed
    part = _create_part(c, "R10k")
    storage = _create_storage(c)
    _add_stock(c, part, 100, storage)

    project_id = _project(c, "PCB-ZERO")
    entry = _add_entry(c, project_id, part_id=part, quantity=10)
    assert entry["attrition_pct"] == 0

    bid = _build(c, project_id, quantity=5)
    shortage = c.get(f"/api/builds/{bid}").json()["data"]["shortage"]
    row = next(r for r in shortage if r["part_id"] == part)
    assert row["required"] == 50, row  # 10 * 5, unchanged


# --- Reservations are sized by the attrition-adjusted requirement -------------


def test_reservation_sized_by_attrition(authed):
    c = authed
    part = _create_part(c, "R4k7")
    storage = _create_storage(c)
    _add_stock(c, part, 200, storage)

    project_id = _project(c, "PCB-RES")
    _add_entry(c, project_id, part_id=part, quantity=100, attrition_pct=2.5)

    # Creating the build applies reservations sized by _required → 103.
    _build(c, project_id, quantity=1)
    p = c.get(f"/api/parts/{part}").json()["data"]
    assert p["on_hand"] == 200
    assert p["reserved"] == 103
    assert p["available"] == 97


# --- Consume must require the attrition-adjusted (ceil) quantity --------------


def test_consume_requires_attrition_adjusted_quantity(authed):
    c = authed
    part = _create_part(c, "R2k2")
    storage = _create_storage(c)
    _add_stock(c, part, 200, storage)

    project_id = _project(c, "PCB-CONS")
    entry = _add_entry(c, project_id, part_id=part, quantity=100, attrition_pct=2.5)

    bid = _build(c, project_id, quantity=1)

    # Supplying only 102 (the un-rounded 102.5 floored) must be rejected —
    # required is 103.
    line = {"project_entry_id": entry["id"], "part_id": part, "quantity": 102, "storage_location_id": storage}
    r = c.post(f"/api/builds/{bid}/consume", json={"lines": [line]})
    assert r.status_code == 400, r.text
    assert "under-consumed" in r.json()["status"]["message"]


def test_consume_accepts_attrition_adjusted_quantity(authed):
    c = authed
    part = _create_part(c, "R3k3")
    storage = _create_storage(c)
    _add_stock(c, part, 200, storage)

    project_id = _project(c, "PCB-CONS-OK")
    entry = _add_entry(c, project_id, part_id=part, quantity=100, attrition_pct=2.5)

    bid = _build(c, project_id, quantity=1)
    line = {"project_entry_id": entry["id"], "part_id": part, "quantity": 103, "storage_location_id": storage}
    r = c.post(f"/api/builds/{bid}/consume", json={"lines": [line]})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "complete"
    # 200 - 103 consumed = 97 on hand
    assert c.get(f"/api/parts/{part}/stock").json()["data"]["total_on_hand"] == 97


# --- Route: set / update / validate attrition_pct -----------------------------


def test_set_attrition_pct_on_create_and_read_back(authed):
    c = authed
    part = _create_part(c, "L1")
    project_id = _project(c, "PROJ-SET")
    entry = _add_entry(c, project_id, part_id=part, quantity=5, attrition_pct=3.75)
    assert entry["attrition_pct"] == 3.75

    listed = c.get(f"/api/projects/{project_id}/entries").json()["data"]
    assert listed[0]["attrition_pct"] == 3.75


def test_patch_updates_attrition_pct(authed):
    c = authed
    part = _create_part(c, "L2")
    project_id = _project(c, "PROJ-PATCH")
    entry = _add_entry(c, project_id, part_id=part, quantity=5)
    assert entry["attrition_pct"] == 0

    r = c.patch(f"/api/projects/{project_id}/entries/{entry['id']}", json={"attrition_pct": 5})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["attrition_pct"] == 5

    # Omitting attrition_pct on a later patch must leave it untouched.
    r = c.patch(f"/api/projects/{project_id}/entries/{entry['id']}", json={"comments": "x"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["attrition_pct"] == 5


@pytest.mark.parametrize("bad", [-1, 100, 150])
def test_create_rejects_out_of_range_attrition(authed, bad):
    c = authed
    part = _create_part(c, f"BAD-{bad}")
    project_id = _project(c, f"PROJ-BAD-{bad}")
    r = c.post(
        f"/api/projects/{project_id}/entries",
        json={"part_id": part, "quantity": 1, "attrition_pct": bad},
    )
    assert r.status_code == 422, r.text
    assert r.json()["status"]["category"] == "validation_error"


def test_patch_rejects_out_of_range_attrition(authed):
    c = authed
    part = _create_part(c, "PATCH-BAD")
    project_id = _project(c, "PROJ-PATCH-BAD")
    entry = _add_entry(c, project_id, part_id=part, quantity=1)
    r = c.patch(f"/api/projects/{project_id}/entries/{entry['id']}", json={"attrition_pct": 100})
    assert r.status_code == 422, r.text
