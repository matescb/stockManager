"""Bulk-delete (archive) parts from a multi-select.

Hard delete is intentionally out of scope — parts have FK references
into stock_entries, lots, order_entries, bom_entries; cascading would
destroy ledger history. Bulk-delete = bulk-archive, mirroring the
existing single-row archive flow.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient) -> None:
    r = c.post(
        "/api/auth/signup",
        json={"email": f"u-{uuid.uuid4().hex[:8]}@x.com", "name": "u", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text


@pytest.fixture
def authed():
    c = TestClient(app)
    _signup(c)
    return c


def _create(c: TestClient, name: str, mpn: str | None = None) -> str:
    body = {"name": name}
    if mpn:
        body["mpn"] = mpn
    r = c.post("/api/parts", json=body)
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def test_bulk_delete_archives_listed_parts(authed):
    a = _create(authed, "A")
    b = _create(authed, "B")
    c = _create(authed, "C")
    r = authed.post("/api/parts/bulk-delete", json={"part_ids": [a, c]})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert sorted(body["archived_ids"]) == sorted([a, c])
    assert body["already_archived_ids"] == []
    assert body["not_found_ids"] == []

    # B is still present in the active list; A and C only show in /archived.
    actives = [p["id"] for p in authed.get("/api/parts").json()["data"]]
    assert b in actives
    assert a not in actives and c not in actives
    archived_ids = [p["id"] for p in authed.get("/api/parts?archived=true").json()["data"]]
    assert sorted(archived_ids) == sorted([a, c])


def test_bulk_delete_skips_already_archived(authed):
    a = _create(authed, "A")
    authed.post(f"/api/parts/{a}/archive")
    r = authed.post("/api/parts/bulk-delete", json={"part_ids": [a]})
    assert r.status_code == 200
    body = r.json()["data"]
    # Already-archived rows aren't re-stamped — they land in already_archived_ids.
    assert body["archived_ids"] == []
    assert body["already_archived_ids"] == [a]
    assert body["not_found_ids"] == []


def test_bulk_delete_silently_skips_other_workspace(authed):
    """Cross-workspace ids must not leak — the route returns the same
    shape as if those ids didn't exist (not_found_ids bucket)."""
    other = TestClient(app)
    _signup(other)
    other_id = _create(other, "Theirs")

    mine = _create(authed, "Mine")
    r = authed.post("/api/parts/bulk-delete", json={"part_ids": [mine, other_id]})
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["archived_ids"] == [mine]
    assert body["already_archived_ids"] == []
    assert body["not_found_ids"] == [other_id]


def test_bulk_delete_rejects_empty_list(authed):
    r = authed.post("/api/parts/bulk-delete", json={"part_ids": []})
    assert r.status_code == 422


def test_bulk_delete_rejects_too_many(authed):
    fake_ids = [str(uuid.uuid4()) for _ in range(101)]
    r = authed.post("/api/parts/bulk-delete", json={"part_ids": fake_ids})
    assert r.status_code == 422


def test_list_parts_includes_image_url(authed):
    """When a part has an image_url custom_field, the list endpoint
    surfaces it as Part.image_url so the parts table can render a
    thumbnail without a per-row custom_field fetch."""
    pid = _create(authed, "Resistor", mpn="RC0402JR-070R")
    # Backdoor: write the custom_field directly via the API.
    r = authed.post(
        "/api/custom-fields",
        json={
            "object_type": "part",
            "object_id": pid,
            "key": "image_url",
            "value": "/api/parts/assets/abc/image.png",
        },
    )
    assert r.status_code in (200, 201), r.text
    listed = authed.get("/api/parts").json()["data"]
    row = next(r for r in listed if r["id"] == pid)
    assert row["image_url"] == "/api/parts/assets/abc/image.png"
