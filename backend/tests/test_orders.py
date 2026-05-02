from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient, email: str | None = None):
    email = email or f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post("/api/auth/signup", json={"email": email, "name": "u", "password": "TestPass-2026-Stronk"})
    assert r.status_code == 200, r.text


@pytest.fixture
def authed():
    c = TestClient(app)
    _signup(c)
    return c


@pytest.fixture
def part_and_storage(authed):
    p = authed.post("/api/parts", json={"name": "Cap 0.1uF", "part_type": "local"})
    assert p.status_code in (200, 201), p.text
    part_id = p.json()["data"]["id"]

    s = authed.post("/api/storage", json={"name": "Shelf A"})
    assert s.status_code in (200, 201), s.text
    storage_id = s.json()["data"]["id"]
    return authed, part_id, storage_id


def test_create_empty_order_starts_in_draft(authed):
    r = authed.post("/api/orders", json={"name": "PO-001"})
    assert r.status_code == 201, r.text
    o = r.json()["data"]
    assert o["status"] == "draft"
    assert o["totals"] == {"ordered": 0, "received": 0}


def test_create_with_entries_marks_open(part_and_storage):
    c, part_id, _ = part_and_storage
    r = c.post(
        "/api/orders",
        json={
            "name": "PO-002",
            "supplier": "Acme",
            "currency": "USD",
            "entries": [
                {"part_id": part_id, "quantity_ordered": 100, "unit_price": "0.05"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    o = r.json()["data"]
    assert o["status"] == "open"
    assert o["totals"] == {"ordered": 100, "received": 0}


def test_partial_then_full_receive(part_and_storage):
    c, part_id, storage_id = part_and_storage
    r = c.post(
        "/api/orders",
        json={
            "name": "PO-003",
            "currency": "USD",
            "entries": [
                {"part_id": part_id, "quantity_ordered": 10, "unit_price": "1.50"},
            ],
        },
    )
    order = r.json()["data"]
    detail = c.get(f"/api/orders/{order['id']}").json()["data"]
    entry_id = detail["entries"][0]["id"]

    # Partial receive (4 of 10)
    r = c.post(
        f"/api/orders/{order['id']}/receive",
        json={"lines": [{"order_entry_id": entry_id, "quantity": 4, "storage_location_id": storage_id}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "partial"
    assert len(r.json()["data"]["lots"]) == 1

    # Full receive remainder (6 of 10)
    r = c.post(
        f"/api/orders/{order['id']}/receive",
        json={"lines": [{"order_entry_id": entry_id, "quantity": 6, "storage_location_id": storage_id}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "received"

    # Stock should be 10 on hand
    parts = c.get(f"/api/parts/{part_id}/stock").json()["data"]
    assert parts["total_on_hand"] == 10

    # Two lots created, both with source_type=purchase
    lots = c.get(f"/api/parts/{part_id}/lots").json()["data"]
    assert len(lots) == 2
    assert all(l["source_type"] == "purchase" for l in lots)
    # Unit cost preserved
    assert all(l["purchase_unit_cost"] == 1.5 for l in lots)


def test_over_receive_rejected(part_and_storage):
    c, part_id, storage_id = part_and_storage
    r = c.post(
        "/api/orders",
        json={"name": "PO-004", "entries": [{"part_id": part_id, "quantity_ordered": 5}]},
    )
    order = r.json()["data"]
    entry_id = c.get(f"/api/orders/{order['id']}").json()["data"]["entries"][0]["id"]

    r = c.post(
        f"/api/orders/{order['id']}/receive",
        json={"lines": [{"order_entry_id": entry_id, "quantity": 6, "storage_location_id": storage_id}]},
    )
    assert r.status_code == 400, r.text
    assert "over-receive" in r.json()["status"]["message"]


def test_cannot_receive_unmatched_entry(authed, part_and_storage):
    c, _, storage_id = part_and_storage
    r = c.post(
        "/api/orders",
        json={"name": "PO-005", "entries": [{"name": "TBD", "quantity_ordered": 3}]},
    )
    order = r.json()["data"]
    entry_id = c.get(f"/api/orders/{order['id']}").json()["data"]["entries"][0]["id"]

    r = c.post(
        f"/api/orders/{order['id']}/receive",
        json={"lines": [{"order_entry_id": entry_id, "quantity": 3, "storage_location_id": storage_id}]},
    )
    assert r.status_code == 400


def test_cannot_delete_partially_received_entry(part_and_storage):
    c, part_id, storage_id = part_and_storage
    r = c.post(
        "/api/orders",
        json={"name": "PO-006", "entries": [{"part_id": part_id, "quantity_ordered": 5}]},
    )
    order = r.json()["data"]
    entry_id = c.get(f"/api/orders/{order['id']}").json()["data"]["entries"][0]["id"]
    c.post(
        f"/api/orders/{order['id']}/receive",
        json={"lines": [{"order_entry_id": entry_id, "quantity": 1, "storage_location_id": storage_id}]},
    )

    r = c.delete(f"/api/orders/{order['id']}/entries/{entry_id}")
    assert r.status_code == 400


def test_archive_restore(authed):
    r = authed.post("/api/orders", json={"name": "PO-007"}).json()["data"]
    assert authed.post(f"/api/orders/{r['id']}/archive").status_code == 200
    out = authed.get("/api/orders").json()["data"]
    assert all(o["id"] != r["id"] for o in out)
    out_arch = authed.get("/api/orders?archived=true").json()["data"]
    assert any(o["id"] == r["id"] for o in out_arch)
    assert authed.post(f"/api/orders/{r['id']}/restore").status_code == 200


# ---------------------------------------------------------------------------
# BE2-013 — quantity_ordered must be >= 1
# ---------------------------------------------------------------------------


def test_create_with_zero_quantity_rejected(part_and_storage):
    c, part_id, _ = part_and_storage
    r = c.post(
        "/api/orders",
        json={"name": "PO-zero", "entries": [{"part_id": part_id, "quantity_ordered": 0}]},
    )
    assert r.status_code == 422, r.text


def test_create_with_negative_quantity_rejected(part_and_storage):
    c, part_id, _ = part_and_storage
    r = c.post(
        "/api/orders",
        json={"name": "PO-neg", "entries": [{"part_id": part_id, "quantity_ordered": -5}]},
    )
    assert r.status_code == 422, r.text


def test_patch_entry_to_zero_quantity_rejected(part_and_storage):
    c, part_id, _ = part_and_storage
    r = c.post(
        "/api/orders",
        json={"name": "PO-pat", "entries": [{"part_id": part_id, "quantity_ordered": 5}]},
    ).json()["data"]
    entry_id = c.get(f"/api/orders/{r['id']}").json()["data"]["entries"][0]["id"]
    p = c.patch(f"/api/orders/{r['id']}/entries/{entry_id}", json={"quantity_ordered": 0})
    assert p.status_code == 422, p.text
