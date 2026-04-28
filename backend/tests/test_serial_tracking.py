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


def _enable_serial_tracking(c):
    r = c.patch("/api/workspaces/current", json={"serial_tracking_enabled": True})
    assert r.status_code == 200, r.text


def test_workspace_patch_toggles_serial_tracking(authed):
    c = authed
    cur = c.get("/api/workspaces/current").json()["data"]
    assert cur["serial_tracking_enabled"] is False
    _enable_serial_tracking(c)
    cur = c.get("/api/workspaces/current").json()["data"]
    assert cur["serial_tracking_enabled"] is True


def test_serial_required_for_serialized_part_when_workspace_enabled(authed):
    c = authed
    _enable_serial_tracking(c)
    p = c.post("/api/parts", json={"name": "Board", "part_type": "local", "serialized": True}).json()["data"]["id"]
    storage = c.post("/api/storage", json={"name": "Shelf"}).json()["data"]["id"]

    # Missing lot — rejected
    r = c.post("/api/stock/add", json={"part_id": p, "quantity": 1, "storage_location_id": storage})
    assert r.status_code == 400
    assert "serial_number" in r.json()["status"]["message"]

    # Quantity > 1 — rejected
    r = c.post(
        "/api/stock/add",
        json={
            "part_id": p, "quantity": 5, "storage_location_id": storage,
            "lot": {"serial_number": "SN-001"},
        },
    )
    assert r.status_code == 400

    # Quantity 1 + serial — accepted
    r = c.post(
        "/api/stock/add",
        json={
            "part_id": p, "quantity": 1, "storage_location_id": storage,
            "lot": {"serial_number": "SN-001"},
        },
    )
    assert r.status_code == 200, r.text
    lots = c.get(f"/api/parts/{p}/lots").json()["data"]
    assert len(lots) == 1
    assert lots[0]["serial_number"] == "SN-001"


def test_serial_not_enforced_when_workspace_off(authed):
    c = authed
    p = c.post("/api/parts", json={"name": "Board", "part_type": "local", "serialized": True}).json()["data"]["id"]
    storage = c.post("/api/storage", json={"name": "Shelf"}).json()["data"]["id"]
    # Workspace flag default off, so this passes even though part is serialized
    r = c.post("/api/stock/add", json={"part_id": p, "quantity": 5, "storage_location_id": storage})
    assert r.status_code == 200, r.text


def test_serial_required_on_receive(authed):
    c = authed
    _enable_serial_tracking(c)
    p = c.post("/api/parts", json={"name": "Board", "part_type": "local", "serialized": True}).json()["data"]["id"]
    storage = c.post("/api/storage", json={"name": "Shelf"}).json()["data"]["id"]
    o = c.post(
        "/api/orders",
        json={"name": "PO-S1", "entries": [{"part_id": p, "quantity_ordered": 2}]},
    ).json()["data"]
    eid = c.get(f"/api/orders/{o['id']}").json()["data"]["entries"][0]["id"]

    # Missing serial — rejected
    r = c.post(
        f"/api/orders/{o['id']}/receive",
        json={"lines": [{"order_entry_id": eid, "quantity": 1, "storage_location_id": storage}]},
    )
    assert r.status_code == 400
    assert "serial_number" in r.json()["status"]["message"]

    # Quantity > 1 per line — rejected
    r = c.post(
        f"/api/orders/{o['id']}/receive",
        json={"lines": [{"order_entry_id": eid, "quantity": 2, "storage_location_id": storage, "serial_number": "SN-A"}]},
    )
    assert r.status_code == 400

    # Two single-unit lines — accepted
    r = c.post(
        f"/api/orders/{o['id']}/receive",
        json={
            "lines": [
                {"order_entry_id": eid, "quantity": 1, "storage_location_id": storage, "serial_number": "SN-A"},
                {"order_entry_id": eid, "quantity": 1, "storage_location_id": storage, "serial_number": "SN-B"},
            ]
        },
    )
    assert r.status_code == 200, r.text

    lots = c.get(f"/api/parts/{p}/lots").json()["data"]
    serials = sorted([l["serial_number"] for l in lots])
    assert serials == ["SN-A", "SN-B"]
