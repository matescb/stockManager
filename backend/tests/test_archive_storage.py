"""Regression tests for BE2-014 — archive_storage refuses when on-hand
stock is still in residence.

The previous shape would happily set `archived_at`, hiding the storage
from the UI while leaving stock_entries pinned to that storage_id.
Re-listing the storage parts then required showing archived rows or
running a manual SQL fix-up. The fix returns a structured 409 with
`blocking: [{part_id, lot_id, quantity}]` so the UI can show the
operator what they need to move first.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient) -> None:
    email = f"arch-{uuid.uuid4().hex[:6]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text


@pytest.fixture
def authed():
    c = TestClient(app)
    _signup(c)
    return c


def _create_part(c: TestClient, name: str) -> str:
    r = c.post("/api/parts", json={"name": name, "part_type": "local"})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _create_storage(c: TestClient, name: str) -> str:
    r = c.post("/api/storage", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def test_archive_empty_storage_succeeds(authed):
    sid = _create_storage(authed, "Empty Shelf")
    r = authed.post(f"/api/storage/{sid}/archive")
    assert r.status_code == 200, r.text


def test_archive_with_on_hand_stock_409s_with_blocking_detail(authed):
    pid = _create_part(authed, "Cap 0.1uF")
    sid = _create_storage(authed, "Shelf-with-stock")

    add = authed.post(
        "/api/stock/add",
        json={"part_id": pid, "quantity": 10, "storage_location_id": sid},
    )
    assert add.status_code in (200, 201), add.text

    r = authed.post(f"/api/storage/{sid}/archive")
    assert r.status_code == 409, r.text
    body = r.json()
    # http_exception_handler spreads the detail dict onto the body, so
    # `blocking` is at the top level alongside `status` (see
    # core/responses.py).
    assert "blocking" in body, body
    assert isinstance(body["blocking"], list)
    assert len(body["blocking"]) >= 1
    row = body["blocking"][0]
    assert row["part_id"] == pid
    assert int(row["quantity"]) == 10


def test_archive_after_full_consume_succeeds(authed):
    """After moving all stock out, the same storage can be archived."""
    pid = _create_part(authed, "Cap 1uF")
    sid_a = _create_storage(authed, "Shelf A")
    sid_b = _create_storage(authed, "Shelf B")

    authed.post(
        "/api/stock/add",
        json={"part_id": pid, "quantity": 5, "storage_location_id": sid_a},
    )
    mv = authed.post(
        "/api/stock/move",
        json={
            "part_id": pid,
            "quantity": 5,
            "source_storage_location_id": sid_a,
            "destination_storage_location_id": sid_b,
        },
    )
    assert mv.status_code in (200, 201), mv.text

    r = authed.post(f"/api/storage/{sid_a}/archive")
    assert r.status_code == 200, r.text
