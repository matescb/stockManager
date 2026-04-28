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


def test_upload_list_download_delete(authed):
    part_id = _make_part(authed)
    r = authed.post(
        "/api/attachments",
        files={"file": ("test.txt", b"hello", "text/plain")},
        data={"object_type": "part", "object_id": part_id, "file_type": "datasheet"},
    )
    assert r.status_code == 201, r.text
    a = r.json()["data"]
    assert a["file_name"] == "test.txt"
    assert a["object_type"] == "part"
    assert a["file_type"] == "datasheet"
    assert a["size_bytes"] == 5
    aid = a["id"]

    listed = authed.get(f"/api/attachments/by-object/part/{part_id}").json()["data"]
    assert len(listed) == 1
    assert listed[0]["id"] == aid

    dl = authed.get(f"/api/attachments/{aid}/download")
    assert dl.status_code == 200
    assert dl.content == b"hello"

    rd = authed.delete(f"/api/attachments/{aid}")
    assert rd.status_code == 200
    assert authed.get(f"/api/attachments/by-object/part/{part_id}").json()["data"] == []


def test_workspace_isolation(authed):
    part_id = _make_part(authed)
    a = authed.post(
        "/api/attachments",
        files={"file": ("a.txt", b"secret", "text/plain")},
        data={"object_type": "part", "object_id": part_id, "file_type": "other"},
    ).json()["data"]
    aid = a["id"]

    other = TestClient(app)
    _signup(other)
    # Other workspace cannot see the attachment in its own listing
    listed = other.get(f"/api/attachments/by-object/part/{part_id}").json()["data"]
    assert listed == []

    # Direct download is denied as 404
    r = other.get(f"/api/attachments/{aid}/download")
    assert r.status_code == 404

    # Delete from another workspace also denied as 404
    r = other.delete(f"/api/attachments/{aid}")
    assert r.status_code == 404

    # Owner workspace still has it
    still = authed.get(f"/api/attachments/by-object/part/{part_id}").json()["data"]
    assert len(still) == 1
