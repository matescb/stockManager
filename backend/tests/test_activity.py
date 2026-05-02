from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient, email: str | None = None, name: str = "Tester"):
    email = email or f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup", json={"email": email, "name": name, "password": "TestPass-2026-Stronk"}
    )
    assert r.status_code == 200, r.text


@pytest.fixture
def authed():
    c = TestClient(app)
    _signup(c, name="Alice")
    return c


def _make_part(c, name="Cap"):
    r = c.post("/api/parts", json={"name": name, "part_type": "local"})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _make_storage(c, name="Shelf"):
    r = c.post("/api/storage", json={"name": name})
    assert r.status_code in (200, 201)
    return r.json()["data"]["id"]


def test_part_activity_orders_desc_and_includes_creation(authed):
    part_id = _make_part(authed, name="R1k")
    storage_id = _make_storage(authed)

    # Add stock
    r = authed.post(
        "/api/stock/add",
        json={"part_id": part_id, "quantity": 10, "storage_location_id": storage_id},
    )
    assert r.status_code == 200, r.text

    # Remove some
    r = authed.post(
        "/api/stock/remove",
        json={"part_id": part_id, "quantity": 3, "storage_location_id": storage_id},
    )
    assert r.status_code == 200, r.text

    r = authed.get(f"/api/parts/{part_id}/activity")
    assert r.status_code == 200, r.text
    page = r.json()["data"]
    events = page["events"]

    kinds = [e["kind"] for e in events]
    ops = [e["operation_type"] for e in events]
    assert "part_created" in kinds
    assert kinds.count("stock") == 2
    assert "add" in ops
    assert "remove" in ops

    # Sorted desc
    timestamps = [e["occurred_at"] for e in events]
    assert timestamps == sorted(timestamps, reverse=True)

    # Each stock event carries quantity_delta
    stock_events = [e for e in events if e["kind"] == "stock"]
    assert all(e["quantity_delta"] is not None for e in stock_events)
    # User name surfaces on stock events
    assert any(e["user"] and e["user"]["name"] == "Alice" for e in stock_events)


def test_order_activity_shows_create_and_receive(authed):
    part_id = _make_part(authed, name="LED")
    storage_id = _make_storage(authed)
    r = authed.post(
        "/api/orders",
        json={
            "name": "PO-X",
            "currency": "USD",
            "entries": [{"part_id": part_id, "quantity_ordered": 10}],
        },
    )
    assert r.status_code == 201, r.text
    order = r.json()["data"]
    detail = authed.get(f"/api/orders/{order['id']}").json()["data"]
    entry_id = detail["entries"][0]["id"]

    r = authed.post(
        f"/api/orders/{order['id']}/receive",
        json={
            "lines": [
                {
                    "order_entry_id": entry_id,
                    "quantity": 4,
                    "storage_location_id": storage_id,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text

    r = authed.get(f"/api/orders/{order['id']}/activity")
    assert r.status_code == 200
    page = r.json()["data"]
    events = page["events"]
    kinds = [e["kind"] for e in events]
    assert "order_created" in kinds
    stock_events = [e for e in events if e["kind"] == "stock"]
    assert len(stock_events) >= 1
    assert any(e["operation_type"] == "receive" for e in stock_events)

    # Created-by user name shows up on the order_created event
    created = next(e for e in events if e["kind"] == "order_created")
    assert created["user"] is not None
    assert created["user"]["name"] == "Alice"


def test_build_activity_includes_reserve_and_consume(authed):
    p1 = _make_part(authed, name="R1k")
    storage_id = _make_storage(authed)
    r = authed.post(
        "/api/stock/add",
        json={"part_id": p1, "quantity": 100, "storage_location_id": storage_id},
    )
    assert r.status_code == 200, r.text

    r = authed.post("/api/projects", json={"name": "Proj"})
    pid = r.json()["data"]["id"]
    r = authed.post(
        f"/api/projects/{pid}/entries", json={"part_id": p1, "quantity": 5}
    )
    assert r.status_code in (200, 201)

    r = authed.post(
        "/api/builds", json={"name": "B-1", "project_id": pid, "quantity": 4}
    )
    assert r.status_code == 201, r.text
    bid = r.json()["data"]["id"]

    entry = authed.get(f"/api/projects/{pid}/entries").json()["data"][0]
    r = authed.post(
        f"/api/builds/{bid}/consume",
        json={
            "lines": [
                {
                    "project_entry_id": entry["id"],
                    "part_id": p1,
                    "quantity": 20,
                    "storage_location_id": storage_id,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text

    r = authed.get(f"/api/builds/{bid}/activity")
    assert r.status_code == 200
    page = r.json()["data"]
    events = page["events"]
    kinds = [e["kind"] for e in events]
    ops = [e["operation_type"] for e in events]
    assert "build_created" in kinds
    # Reservation row was written on build creation, then released, then
    # build_consume on consume — assert all three.
    assert "reserve" in ops
    assert "release" in ops
    assert "build_consume" in ops


def test_activity_workspace_isolated():
    a = TestClient(app)
    b = TestClient(app)
    _signup(a, f"a-{uuid.uuid4().hex[:6]}@x.com")
    _signup(b, f"b-{uuid.uuid4().hex[:6]}@x.com")

    part_id = _make_part(a)
    # Workspace B cannot read activity for A's part
    r = b.get(f"/api/parts/{part_id}/activity")
    assert r.status_code == 404

    # Build activity isolation
    r = a.post("/api/projects", json={"name": "P"})
    pid = r.json()["data"]["id"]
    r = a.post(f"/api/projects/{pid}/entries", json={"part_id": part_id, "quantity": 1})
    assert r.status_code in (200, 201)
    r = a.post("/api/builds", json={"name": "B", "project_id": pid, "quantity": 1})
    bid = r.json()["data"]["id"]
    r = b.get(f"/api/builds/{bid}/activity")
    assert r.status_code == 404

    # Order activity isolation
    r = a.post("/api/orders", json={"name": "PO-iso"})
    oid = r.json()["data"]["id"]
    r = b.get(f"/api/orders/{oid}/activity")
    assert r.status_code == 404
