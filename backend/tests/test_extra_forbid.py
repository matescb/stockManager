from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient, email: str | None = None) -> None:
    email = email or f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": "password123"},
    )
    assert r.status_code == 200, r.text


@pytest.fixture
def authed():
    c = TestClient(app)
    _signup(c)
    return c


def test_parts_post_rejects_unknown_field_type(authed):
    # `type` is not a valid input field — `part_type` is. Should be 422.
    r = authed.post("/api/parts", json={"name": "X", "type": "meta"})
    assert r.status_code == 422, r.text
    body_text = r.text
    assert "type" in body_text


def test_parts_post_rejects_random_field(authed):
    r = authed.post("/api/parts", json={"banana": "yellow", "name": "X"})
    assert r.status_code == 422, r.text
    assert "banana" in r.text


def test_orders_post_rejects_currency_typo(authed):
    r = authed.post("/api/orders", json={"name": "PO", "currency_typo": "USD"})
    assert r.status_code == 422, r.text
    assert "currency_typo" in r.text


def test_stock_add_rejects_top_level_unit_price(authed):
    # `unit_price` belongs nested under `price`, not at the top level.
    part = authed.post("/api/parts", json={"name": "Cap", "part_type": "local"}).json()["data"]
    r = authed.post(
        "/api/stock/add",
        json={"part_id": part["id"], "quantity": 1, "unit_price": 1.0},
    )
    assert r.status_code == 422, r.text
    assert "unit_price" in r.text
