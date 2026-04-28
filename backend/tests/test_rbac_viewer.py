from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient, email: str | None = None) -> tuple[str, str]:
    email = email or f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": "password123"},
    )
    assert r.status_code == 200, r.text
    return email, r.json()["data"]["workspace_id"]


@pytest.fixture
def owner_and_viewer():
    """Owner creates a workspace, invites a user as `viewer`, viewer
    accepts and switches into the shared workspace."""
    owner = TestClient(app)
    _signup(owner)

    invitee_email = f"viewer-{uuid.uuid4().hex[:6]}@x.com"
    inv = owner.post(
        "/api/invitations",
        json={"email": invitee_email, "role": "viewer"},
    ).json()["data"]
    assert inv["role"] == "viewer"
    token = inv["token"]
    assert token

    viewer = TestClient(app)
    viewer.post(
        "/api/auth/signup",
        json={"email": invitee_email, "name": "Vee", "password": "password123"},
    )
    viewer.post("/api/invitations/accept", json={"token": token})

    # Switch viewer client into the shared (owner's) workspace.
    me = viewer.get("/api/auth/me").json()["data"]
    viewer_personal = me["workspaces"][0]["id"]
    wss = viewer.get("/api/workspaces").json()["data"]
    shared = next(w for w in wss if w["id"] != viewer_personal)
    viewer.post(f"/api/workspaces/{shared['id']}/switch")

    return owner, viewer


def test_viewer_blocked_by_router_level_gate(owner_and_viewer):
    """The data routers are gated at member+ (router-level); viewers hit
    403 on reads as well as writes. A viewer role isn't currently wired
    to any read-only surface — the gate is the contract."""
    owner, viewer = owner_and_viewer
    owner.post("/api/parts", json={"name": "Cap", "part_type": "local"})
    r = viewer.get("/api/parts")
    assert r.status_code == 403, r.text


def test_viewer_cannot_create_part(owner_and_viewer):
    _, viewer = owner_and_viewer
    r = viewer.post("/api/parts", json={"name": "X", "part_type": "local"})
    assert r.status_code == 403, r.text


def test_viewer_cannot_add_stock(owner_and_viewer):
    owner, viewer = owner_and_viewer
    part_id = owner.post(
        "/api/parts", json={"name": "Cap", "part_type": "local"}
    ).json()["data"]["id"]
    r = viewer.post("/api/stock/add", json={"part_id": part_id, "quantity": 5})
    assert r.status_code == 403, r.text


def test_viewer_cannot_create_order(owner_and_viewer):
    _, viewer = owner_and_viewer
    r = viewer.post("/api/orders", json={"name": "PO-x"})
    assert r.status_code == 403, r.text


def test_viewer_cannot_create_build(owner_and_viewer):
    owner, viewer = owner_and_viewer
    proj_id = owner.post(
        "/api/projects", json={"name": "Proj"}
    ).json()["data"]["id"]
    r = viewer.post(
        "/api/builds",
        json={"name": "B", "project_id": proj_id, "quantity": 1},
    )
    assert r.status_code == 403, r.text


def test_viewer_cannot_patch_project(owner_and_viewer):
    owner, viewer = owner_and_viewer
    proj_id = owner.post(
        "/api/projects", json={"name": "Proj"}
    ).json()["data"]["id"]
    r = viewer.patch(f"/api/projects/{proj_id}", json={"name": "renamed"})
    assert r.status_code == 403, r.text
