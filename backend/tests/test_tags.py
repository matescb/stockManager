from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient, email: str | None = None) -> str:
    email = email or f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": "password123"},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["workspace_id"]


@pytest.fixture
def authed():
    c = TestClient(app)
    _signup(c)
    return c


def _make_part(c: TestClient) -> str:
    return c.post(
        "/api/parts", json={"name": "Cap", "part_type": "local"}
    ).json()["data"]["id"]


def test_create_link_list_unlink(authed):
    part_id = _make_part(authed)
    r = authed.post("/api/tags", json={"name": "rohs", "color": "#0a0"})
    assert r.status_code == 201, r.text
    tag_id = r.json()["data"]["id"]

    r = authed.post(
        "/api/tags/links",
        json={"tag_id": tag_id, "object_type": "part", "object_id": part_id},
    )
    assert r.status_code == 201, r.text
    link_id = r.json()["data"]["id"]

    listed = authed.get(f"/api/tags/by-object/part/{part_id}").json()["data"]
    assert len(listed) == 1
    assert listed[0]["tag"]["name"] == "rohs"
    assert listed[0]["tag"]["color"] == "#0a0"

    r = authed.delete(f"/api/tags/links/{link_id}")
    assert r.status_code == 200
    assert authed.get(f"/api/tags/by-object/part/{part_id}").json()["data"] == []


def test_link_idempotent(authed):
    part_id = _make_part(authed)
    tag_id = authed.post("/api/tags", json={"name": "wip"}).json()["data"]["id"]
    a = authed.post(
        "/api/tags/links",
        json={"tag_id": tag_id, "object_type": "part", "object_id": part_id},
    ).json()["data"]
    b = authed.post(
        "/api/tags/links",
        json={"tag_id": tag_id, "object_type": "part", "object_id": part_id},
    ).json()["data"]
    assert a["id"] == b["id"]
    listed = authed.get(f"/api/tags/by-object/part/{part_id}").json()["data"]
    assert len(listed) == 1


def test_workspace_isolation(authed):
    part_id = _make_part(authed)
    tag_id = authed.post("/api/tags", json={"name": "secret"}).json()["data"]["id"]
    authed.post(
        "/api/tags/links",
        json={"tag_id": tag_id, "object_type": "part", "object_id": part_id},
    )

    other = TestClient(app)
    _signup(other)
    # Other workspace doesn't see the tag in its own list
    other_tags = other.get("/api/tags").json()["data"]
    assert all(t["id"] != tag_id for t in other_tags)
    # Other workspace doesn't see the tag link either
    other_links = other.get(f"/api/tags/by-object/part/{part_id}").json()["data"]
    assert other_links == []

    # Linking using a foreign tag id is rejected (tag not found in their ws)
    other_part = _make_part(other)
    r = other.post(
        "/api/tags/links",
        json={"tag_id": tag_id, "object_type": "part", "object_id": other_part},
    )
    assert r.status_code == 404
