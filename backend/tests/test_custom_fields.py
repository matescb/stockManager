from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient, email: str | None = None) -> str:
    email = email or f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": "TestPass-2026-Stronk"},
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


def test_create_or_update_upserts_on_same_tuple(authed):
    part_id = _make_part(authed)
    payload = {"object_type": "part", "object_id": part_id, "key": "tolerance", "value": "5%"}
    r = authed.post("/api/custom-fields", json=payload)
    assert r.status_code == 201, r.text
    first = r.json()["data"]

    # Same (object_type, object_id, key) tuple — should update, not duplicate.
    payload["value"] = "1%"
    r = authed.post("/api/custom-fields", json=payload)
    assert r.status_code == 201, r.text
    second = r.json()["data"]
    assert second["id"] == first["id"]
    assert second["value"] == "1%"

    listed = authed.get(f"/api/custom-fields/by-object/part/{part_id}").json()["data"]
    assert len(listed) == 1
    assert listed[0]["value"] == "1%"


def test_list_by_object_returns_only_matching(authed):
    part_a = _make_part(authed)
    part_b = _make_part(authed)
    authed.post(
        "/api/custom-fields",
        json={"object_type": "part", "object_id": part_a, "key": "k1", "value": "v1"},
    )
    authed.post(
        "/api/custom-fields",
        json={"object_type": "part", "object_id": part_b, "key": "k2", "value": "v2"},
    )

    a_rows = authed.get(f"/api/custom-fields/by-object/part/{part_a}").json()["data"]
    b_rows = authed.get(f"/api/custom-fields/by-object/part/{part_b}").json()["data"]
    assert {r["key"] for r in a_rows} == {"k1"}
    assert {r["key"] for r in b_rows} == {"k2"}


def test_delete(authed):
    part_id = _make_part(authed)
    cf = authed.post(
        "/api/custom-fields",
        json={"object_type": "part", "object_id": part_id, "key": "k", "value": "v"},
    ).json()["data"]
    r = authed.delete(f"/api/custom-fields/{cf['id']}")
    assert r.status_code == 200
    assert authed.get(f"/api/custom-fields/by-object/part/{part_id}").json()["data"] == []


def test_workspace_isolation(authed):
    part_id = _make_part(authed)
    authed.post(
        "/api/custom-fields",
        json={"object_type": "part", "object_id": part_id, "key": "secret", "value": "hush"},
    )

    other = TestClient(app)
    _signup(other)
    rows = other.get(f"/api/custom-fields/by-object/part/{part_id}").json()["data"]
    assert rows == []
