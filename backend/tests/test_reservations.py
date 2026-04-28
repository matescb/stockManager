"""Reservation-bucket tests for planned builds."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def authed():
    c = TestClient(app)
    r = c.post(
        "/api/auth/signup",
        json={"email": f"u-{uuid.uuid4().hex[:8]}@x.com", "name": "u", "password": "password123"},
    )
    assert r.status_code == 200, r.text
    return c


def _create_part(c, name, **extra):
    r = c.post("/api/parts", json={"name": name, "part_type": "local", **extra})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _create_storage(c, name="Shelf"):
    r = c.post("/api/storage", json={"name": name})
    assert r.status_code in (200, 201)
    return r.json()["data"]["id"]


def _add_stock(c, part_id, qty, storage_id=None):
    body = {"part_id": part_id, "quantity": qty}
    if storage_id:
        body["storage_location_id"] = storage_id
    r = c.post("/api/stock/add", json=body)
    assert r.status_code == 200, r.text


def _create_project_with_bom(c, project_name, bom):
    """bom rows: {part_id, quantity, dnp?, name?}."""
    r = c.post("/api/projects", json={"name": project_name})
    assert r.status_code in (200, 201)
    pid = r.json()["data"]["id"]
    for row in bom:
        body = {
            "quantity": row["quantity"],
            "dnp": row.get("dnp", False),
        }
        if "part_id" in row:
            body["part_id"] = row["part_id"]
        if "name" in row:
            body["name"] = row["name"]
        r = c.post(f"/api/projects/{pid}/entries", json=body)
        assert r.status_code in (200, 201), r.text
    return pid


def _part_get(c, part_id):
    return c.get(f"/api/parts/{part_id}").json()["data"]


def test_create_build_writes_reservations(authed):
    c = authed
    p1 = _create_part(c, "R1k")
    p2 = _create_part(c, "C100n")
    storage = _create_storage(c)
    _add_stock(c, p1, 100, storage)
    _add_stock(c, p2, 100, storage)

    project_id = _create_project_with_bom(
        c, "PCB-RSV",
        [{"part_id": p1, "quantity": 10}, {"part_id": p2, "quantity": 5}],
    )
    r = c.post("/api/builds", json={"name": "B-rsv", "project_id": project_id, "quantity": 6})
    assert r.status_code == 201, r.text

    d1 = _part_get(c, p1)
    d2 = _part_get(c, p2)
    # 10 * 6 = 60 reserved on p1; 5 * 6 = 30 reserved on p2
    assert d1["reserved"] == 60
    assert d2["reserved"] == 30
    assert d1["on_hand"] == 100
    assert d1["available"] == 40
    assert d2["available"] == 70


def test_available_equals_on_hand_minus_reserved(authed):
    c = authed
    p = _create_part(c, "R1k")
    storage = _create_storage(c)
    _add_stock(c, p, 200, storage)
    project = _create_project_with_bom(c, "P", [{"part_id": p, "quantity": 7}])
    c.post("/api/builds", json={"name": "B", "project_id": project, "quantity": 10})
    d = _part_get(c, p)
    assert d["on_hand"] == 200
    assert d["reserved"] == 70
    assert d["available"] == 130


def test_patch_quantity_re_reserves(authed):
    c = authed
    p = _create_part(c, "R1k")
    storage = _create_storage(c)
    _add_stock(c, p, 200, storage)
    project = _create_project_with_bom(c, "P", [{"part_id": p, "quantity": 5}])
    r = c.post("/api/builds", json={"name": "B", "project_id": project, "quantity": 10})
    bid = r.json()["data"]["id"]
    assert _part_get(c, p)["reserved"] == 50

    r = c.patch(f"/api/builds/{bid}", json={"quantity": 4})
    assert r.status_code == 200, r.text
    assert _part_get(c, p)["reserved"] == 20


def test_patch_status_cancelled_releases(authed):
    c = authed
    p = _create_part(c, "R1k")
    storage = _create_storage(c)
    _add_stock(c, p, 100, storage)
    project = _create_project_with_bom(c, "P", [{"part_id": p, "quantity": 3}])
    r = c.post("/api/builds", json={"name": "B", "project_id": project, "quantity": 10})
    bid = r.json()["data"]["id"]
    assert _part_get(c, p)["reserved"] == 30

    r = c.patch(f"/api/builds/{bid}", json={"status": "cancelled"})
    assert r.status_code == 200, r.text
    assert _part_get(c, p)["reserved"] == 0
    # Idempotent: patching cancelled again does not flip the sign.
    c.patch(f"/api/builds/{bid}", json={"status": "cancelled"})
    assert _part_get(c, p)["reserved"] == 0


def test_archive_releases(authed):
    c = authed
    p = _create_part(c, "R1k")
    storage = _create_storage(c)
    _add_stock(c, p, 100, storage)
    project = _create_project_with_bom(c, "P", [{"part_id": p, "quantity": 4}])
    r = c.post("/api/builds", json={"name": "B", "project_id": project, "quantity": 5})
    bid = r.json()["data"]["id"]
    assert _part_get(c, p)["reserved"] == 20

    r = c.post(f"/api/builds/{bid}/archive")
    assert r.status_code == 200, r.text
    assert _part_get(c, p)["reserved"] == 0


def test_consume_releases_first_no_double_count(authed):
    c = authed
    p = _create_part(c, "R1k")
    storage = _create_storage(c)
    _add_stock(c, p, 100, storage)
    project = _create_project_with_bom(c, "P", [{"part_id": p, "quantity": 5}])
    r = c.post("/api/builds", json={"name": "B", "project_id": project, "quantity": 10})
    bid = r.json()["data"]["id"]
    # 50 reserved
    assert _part_get(c, p)["reserved"] == 50
    assert _part_get(c, p)["available"] == 50

    e = c.get(f"/api/projects/{project}/entries").json()["data"][0]
    # Consume needs 50; we have 100 on_hand. If reserved weren't released
    # before consume, the consume itself should still work but reserved
    # bucket would stay at 50 — instead, consume releases first.
    r = c.post(
        f"/api/builds/{bid}/consume",
        json={"lines": [{"project_entry_id": e["id"], "part_id": p, "quantity": 50, "storage_location_id": storage}]},
    )
    assert r.status_code == 200, r.text
    d = _part_get(c, p)
    assert d["on_hand"] == 50
    assert d["reserved"] == 0
    assert d["available"] == 50


def test_dnp_and_unmatched_skipped(authed):
    c = authed
    p1 = _create_part(c, "R1k")
    p2 = _create_part(c, "DNP-cap")
    storage = _create_storage(c)
    _add_stock(c, p1, 100, storage)
    _add_stock(c, p2, 100, storage)
    project = _create_project_with_bom(
        c, "P",
        [
            {"part_id": p1, "quantity": 10},
            {"part_id": p2, "quantity": 5, "dnp": True},
            {"name": "FREEFORM", "quantity": 3},  # unmatched / non-part — no part_id
        ],
    )
    c.post("/api/builds", json={"name": "B", "project_id": project, "quantity": 4})
    assert _part_get(c, p1)["reserved"] == 40
    # DNP entry: not reserved
    assert _part_get(c, p2)["reserved"] == 0


def test_complete_build_returns_reserved_to_zero(authed):
    c = authed
    p1 = _create_part(c, "R1k")
    p2 = _create_part(c, "C100n")
    storage = _create_storage(c)
    _add_stock(c, p1, 100, storage)
    _add_stock(c, p2, 100, storage)
    project = _create_project_with_bom(
        c, "P",
        [{"part_id": p1, "quantity": 5}, {"part_id": p2, "quantity": 2}],
    )
    r = c.post("/api/builds", json={"name": "B", "project_id": project, "quantity": 10})
    bid = r.json()["data"]["id"]
    # 50 + 20 reserved
    assert _part_get(c, p1)["reserved"] == 50
    assert _part_get(c, p2)["reserved"] == 20

    entries = c.get(f"/api/projects/{project}/entries").json()["data"]
    e1 = next(e for e in entries if e["part_id"] == p1)
    e2 = next(e for e in entries if e["part_id"] == p2)
    r = c.post(
        f"/api/builds/{bid}/consume",
        json={
            "lines": [
                {"project_entry_id": e1["id"], "part_id": p1, "quantity": 50, "storage_location_id": storage},
                {"project_entry_id": e2["id"], "part_id": p2, "quantity": 20, "storage_location_id": storage},
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert _part_get(c, p1)["reserved"] == 0
    assert _part_get(c, p2)["reserved"] == 0
    assert _part_get(c, p1)["on_hand"] == 50
    assert _part_get(c, p2)["on_hand"] == 80


def test_low_stock_report_uses_available(authed):
    c = authed
    # threshold 10; on_hand 50; if we reserve 45, available=5 → low.
    p = _create_part(c, "R-thresh", low_stock_report_quantity=10)
    storage = _create_storage(c)
    _add_stock(c, p, 50, storage)
    # First, no build → not low.
    rows = c.get("/api/reports/low-stock").json()["data"]
    assert all(r["part_id"] != p for r in rows)

    project = _create_project_with_bom(c, "P", [{"part_id": p, "quantity": 45}])
    c.post("/api/builds", json={"name": "B", "project_id": project, "quantity": 1})

    rows = c.get("/api/reports/low-stock").json()["data"]
    me = next(r for r in rows if r["part_id"] == p)
    assert me["on_hand"] == 50
    assert me["reserved"] == 45
    assert me["available"] == 5
    assert me["short_by"] == 5
