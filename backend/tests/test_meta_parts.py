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


def test_meta_member_crud_and_constraints(authed):
    c = authed
    meta = c.post("/api/parts", json={"name": "0R 0402", "part_type": "meta"}).json()["data"]["id"]
    a = c.post("/api/parts", json={"name": "Yageo 0R", "part_type": "local", "mpn": "RC0402-070R"}).json()["data"]["id"]
    b = c.post("/api/parts", json={"name": "Vishay 0R", "part_type": "local", "mpn": "CRCW04020000Z0"}).json()["data"]["id"]
    plain = c.post("/api/parts", json={"name": "Plain", "part_type": "local"}).json()["data"]["id"]
    other_meta = c.post("/api/parts", json={"name": "100n", "part_type": "meta"}).json()["data"]["id"]

    # Add members
    r = c.post(f"/api/parts/{meta}/members", json={"member_part_id": a})
    assert r.status_code == 201, r.text
    r = c.post(f"/api/parts/{meta}/members", json={"member_part_id": b})
    assert r.status_code == 201, r.text

    # List
    members = c.get(f"/api/parts/{meta}/members").json()["data"]
    assert len(members) == 2
    assert {m["member_part_id"] for m in members} == {a, b}

    # Cannot add self
    r = c.post(f"/api/parts/{meta}/members", json={"member_part_id": meta})
    assert r.status_code == 400

    # Cannot nest meta-parts
    r = c.post(f"/api/parts/{meta}/members", json={"member_part_id": other_meta})
    assert r.status_code == 400

    # Adding a non-meta to a non-meta target rejected (parent must be meta)
    r = c.post(f"/api/parts/{plain}/members", json={"member_part_id": a})
    assert r.status_code == 400

    # Idempotent re-add — count stays the same regardless of status code
    r = c.post(f"/api/parts/{meta}/members", json={"member_part_id": a})
    assert r.status_code in (200, 201)
    members = c.get(f"/api/parts/{meta}/members").json()["data"]
    assert len(members) == 2

    # Delete
    r = c.delete(f"/api/parts/{meta}/members/{a}")
    assert r.status_code == 200
    members = c.get(f"/api/parts/{meta}/members").json()["data"]
    assert len(members) == 1


def test_build_against_meta_part_entry(authed):
    c = authed
    meta = c.post("/api/parts", json={"name": "0R 0402", "part_type": "meta"}).json()["data"]["id"]
    a = c.post("/api/parts", json={"name": "Yageo 0R", "part_type": "local"}).json()["data"]["id"]
    b = c.post("/api/parts", json={"name": "Vishay 0R", "part_type": "local"}).json()["data"]["id"]
    c.post(f"/api/parts/{meta}/members", json={"member_part_id": a})
    c.post(f"/api/parts/{meta}/members", json={"member_part_id": b})

    storage = c.post("/api/storage", json={"name": "Shelf"}).json()["data"]["id"]
    c.post("/api/stock/add", json={"part_id": a, "quantity": 30, "storage_location_id": storage})
    c.post("/api/stock/add", json={"part_id": b, "quantity": 50, "storage_location_id": storage})

    proj = c.post("/api/projects", json={"name": "Meta-Test"}).json()["data"]["id"]
    # BOM line uses the meta-part directly, with entry_type 'meta_part'
    r = c.post(
        f"/api/projects/{proj}/entries",
        json={"part_id": meta, "quantity": 10, "entry_type": "meta_part"},
    )
    assert r.status_code in (200, 201), r.text

    r = c.post("/api/builds", json={"name": "B-meta", "project_id": proj, "quantity": 5})
    bid = r.json()["data"]["id"]

    # Shortage: required 50, "available" on the meta itself = 0,
    # but substitute_available should sum members = 80
    detail = c.get(f"/api/builds/{bid}").json()["data"]
    s = detail["shortage"][0]
    assert s["required"] == 50
    assert s["available"] == 0
    assert s["substitute_available"] == 80
    assert s["short_by"] == 0

    e = c.get(f"/api/projects/{proj}/entries").json()["data"][0]
    # Consume from members: 30 of a, 20 of b
    r = c.post(
        f"/api/builds/{bid}/consume",
        json={
            "lines": [
                {"project_entry_id": e["id"], "part_id": a, "quantity": 30, "storage_location_id": storage},
                {"project_entry_id": e["id"], "part_id": b, "quantity": 20, "storage_location_id": storage},
            ]
        },
    )
    assert r.status_code == 200, r.text
    # Stock decremented
    assert c.get(f"/api/parts/{a}/stock").json()["data"]["total_on_hand"] == 0
    assert c.get(f"/api/parts/{b}/stock").json()["data"]["total_on_hand"] == 30


def test_consume_meta_with_non_member_rejected(authed):
    c = authed
    meta = c.post("/api/parts", json={"name": "0R 0402", "part_type": "meta"}).json()["data"]["id"]
    member = c.post("/api/parts", json={"name": "Yageo", "part_type": "local"}).json()["data"]["id"]
    outsider = c.post("/api/parts", json={"name": "Stranger", "part_type": "local"}).json()["data"]["id"]
    c.post(f"/api/parts/{meta}/members", json={"member_part_id": member})

    storage = c.post("/api/storage", json={"name": "Shelf"}).json()["data"]["id"]
    c.post("/api/stock/add", json={"part_id": outsider, "quantity": 100, "storage_location_id": storage})

    proj = c.post("/api/projects", json={"name": "P"}).json()["data"]["id"]
    c.post(f"/api/projects/{proj}/entries", json={"part_id": meta, "quantity": 5, "entry_type": "meta_part"})

    r = c.post("/api/builds", json={"name": "B", "project_id": proj, "quantity": 1})
    bid = r.json()["data"]["id"]
    e = c.get(f"/api/projects/{proj}/entries").json()["data"][0]

    r = c.post(
        f"/api/builds/{bid}/consume",
        json={"lines": [{"project_entry_id": e["id"], "part_id": outsider, "quantity": 5, "storage_location_id": storage}]},
    )
    assert r.status_code == 400
    assert "meta-part member" in r.json()["status"]["message"]
