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
        json={"email": f"u-{uuid.uuid4().hex[:8]}@x.com", "name": "u", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text
    return c


def test_low_stock_excludes_unset_threshold_and_sufficient(authed):
    c = authed
    no_threshold = c.post("/api/parts", json={"name": "no-thr", "part_type": "local"}).json()["data"]["id"]
    has_threshold_low = c.post(
        "/api/parts",
        json={"name": "low", "part_type": "local", "low_stock_report_quantity": 100},
    ).json()["data"]["id"]
    has_threshold_ok = c.post(
        "/api/parts",
        json={"name": "ok", "part_type": "local", "low_stock_report_quantity": 5},
    ).json()["data"]["id"]
    storage = c.post("/api/storage", json={"name": "Shelf"}).json()["data"]["id"]
    # Below threshold
    c.post("/api/stock/add", json={"part_id": has_threshold_low, "quantity": 30, "storage_location_id": storage})
    # Above threshold
    c.post("/api/stock/add", json={"part_id": has_threshold_ok, "quantity": 10, "storage_location_id": storage})
    # No threshold — never reported
    c.post("/api/stock/add", json={"part_id": no_threshold, "quantity": 1, "storage_location_id": storage})

    r = c.get("/api/reports/low-stock")
    assert r.status_code == 200, r.text
    rows = r.json()["data"]
    assert len(rows) == 1
    assert rows[0]["part_id"] == has_threshold_low
    assert rows[0]["short_by"] == 70


def test_stock_value_aggregates_lots_by_currency(authed):
    c = authed
    p1 = c.post("/api/parts", json={"name": "p1", "part_type": "local"}).json()["data"]["id"]
    p2 = c.post("/api/parts", json={"name": "p2", "part_type": "local"}).json()["data"]["id"]
    storage = c.post("/api/storage", json={"name": "Shelf"}).json()["data"]["id"]

    # 100 of p1 at 0.05 USD per
    c.post(
        "/api/stock/add",
        json={
            "part_id": p1, "quantity": 100, "storage_location_id": storage,
            "price": {"mode": "per_component", "unit_price": "0.05", "currency": "USD"},
            "lot": {"name": "p1-lot1"},
        },
    )
    # 50 of p2 at 1.20 EUR per
    c.post(
        "/api/stock/add",
        json={
            "part_id": p2, "quantity": 50, "storage_location_id": storage,
            "price": {"mode": "per_component", "unit_price": "1.20", "currency": "EUR"},
            "lot": {"name": "p2-lot1"},
        },
    )

    r = c.get("/api/reports/stock-value").json()["data"]
    by_cur = {b["currency"]: b["value"] for b in r["by_currency"]}
    assert abs(by_cur["USD"] - 5.0) < 1e-9
    assert abs(by_cur["EUR"] - 60.0) < 1e-9


def test_bom_shortage_uses_same_engine_as_builds(authed):
    c = authed
    p1 = c.post("/api/parts", json={"name": "p1", "part_type": "local"}).json()["data"]["id"]
    storage = c.post("/api/storage", json={"name": "Shelf"}).json()["data"]["id"]
    c.post("/api/stock/add", json={"part_id": p1, "quantity": 30, "storage_location_id": storage})

    proj = c.post("/api/projects", json={"name": "Test"}).json()["data"]["id"]
    c.post(f"/api/projects/{proj}/entries", json={"part_id": p1, "quantity": 10})

    r = c.get(f"/api/reports/bom-shortage?project_id={proj}&quantity=5").json()["data"]
    assert r["quantity"] == 5
    assert r["total_short"] == 20  # need 50, have 30
    assert r["rows"][0]["short_by"] == 20


def test_expiring_lots(authed):
    c = authed
    p1 = c.post("/api/parts", json={"name": "expiring", "part_type": "local"}).json()["data"]["id"]
    storage = c.post("/api/storage", json={"name": "Shelf"}).json()["data"]["id"]
    c.post(
        "/api/stock/add",
        json={
            "part_id": p1, "quantity": 5, "storage_location_id": storage,
            "lot": {"name": "soon", "expiration_date": "2026-05-15"},
        },
    )
    c.post(
        "/api/stock/add",
        json={
            "part_id": p1, "quantity": 5, "storage_location_id": storage,
            "lot": {"name": "later", "expiration_date": "2030-01-01"},
        },
    )
    # Default window 90 days from "today" (frozen 2026-04-28 in test env)
    r = c.get("/api/reports/expiring-lots?days=90").json()["data"]
    names = [row["name"] for row in r]
    assert "soon" in names
    assert "later" not in names
