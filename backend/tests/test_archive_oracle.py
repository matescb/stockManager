"""403/404 oracle on archive/restore endpoints (BE2-009).

Before the BE2-009 fix, archive/restore routes were guarded with
`Depends(require_role("admin"))` ABOVE the resource lookup. A non-admin
in workspace A probing `POST /api/parts/{B's part_id}/archive` got a
403 — telling the prober "this UUID is real somewhere; you just lack
the role". Resource-existence-first + role-second means foreign UUIDs
return 404 and never leak the role distinction.

These tests pin the new ordering for parts / projects / orders /
builds / storage. Each pair: (a) cross-workspace probe with admin
caller → 404, and (b) same-workspace probe with non-admin → 403. The
combined invariant is the oracle is closed.
"""
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


def _two_admins() -> tuple[TestClient, TestClient]:
    """Two unrelated workspaces. Each owner is admin of their own ws."""
    a = TestClient(app)
    b = TestClient(app)
    _signup(a)
    _signup(b)
    return a, b


def _admin_and_member(role: str = "member") -> tuple[TestClient, TestClient]:
    """One workspace, owner + invited non-admin. Used for the 403 leg."""
    owner = TestClient(app)
    _signup(owner)

    invitee_email = f"m-{uuid.uuid4().hex[:6]}@x.com"
    inv = owner.post(
        "/api/invitations",
        json={"email": invitee_email, "role": role},
    ).json()["data"]
    token = inv["token"]
    member = TestClient(app)
    member.post(
        "/api/auth/signup",
        json={"email": invitee_email, "name": "M", "password": "TestPass-2026-Stronk"},
    )
    member.post("/api/invitations/accept", json={"token": token})
    me = member.get("/api/auth/me").json()["data"]
    personal = me["workspaces"][0]["id"]
    wss = member.get("/api/workspaces").json()["data"]
    shared = next(w for w in wss if w["id"] != personal)
    member.post(f"/api/workspaces/{shared['id']}/switch")
    return owner, member


# ---------------------------------------------------------------------------
# parts
# ---------------------------------------------------------------------------


def test_part_archive_foreign_workspace_returns_404_not_403():
    a, b = _two_admins()
    part_b = b.post(
        "/api/parts", json={"name": "B-secret", "part_type": "local"}
    ).json()["data"]["id"]
    # A is admin of their own workspace, but the part lives in B. The
    # response must hide whether the id exists anywhere — 404, not 403.
    r = a.post(f"/api/parts/{part_b}/archive")
    assert r.status_code == 404, r.text


def test_part_archive_member_in_workspace_returns_403():
    owner, member = _admin_and_member(role="member")
    part = owner.post(
        "/api/parts", json={"name": "P", "part_type": "local"}
    ).json()["data"]["id"]
    # Same-ws member: row is real and visible to them; they just lack admin.
    r = member.post(f"/api/parts/{part}/archive")
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------


def test_project_archive_foreign_workspace_returns_404():
    a, b = _two_admins()
    proj_b = b.post("/api/projects", json={"name": "Pb"}).json()["data"]["id"]
    r = a.post(f"/api/projects/{proj_b}/archive")
    assert r.status_code == 404, r.text


def test_project_restore_member_in_workspace_returns_403():
    owner, member = _admin_and_member(role="member")
    proj = owner.post("/api/projects", json={"name": "P"}).json()["data"]["id"]
    owner.post(f"/api/projects/{proj}/archive")
    r = member.post(f"/api/projects/{proj}/restore")
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# orders
# ---------------------------------------------------------------------------


def test_order_archive_foreign_workspace_returns_404():
    a, b = _two_admins()
    order_b = b.post("/api/orders", json={"name": "OB"}).json()["data"]["id"]
    r = a.post(f"/api/orders/{order_b}/archive")
    assert r.status_code == 404, r.text


def test_order_archive_member_in_workspace_returns_403():
    owner, member = _admin_and_member(role="member")
    order = owner.post("/api/orders", json={"name": "OO"}).json()["data"]["id"]
    r = member.post(f"/api/orders/{order}/archive")
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# builds
# ---------------------------------------------------------------------------


def test_build_archive_foreign_workspace_returns_404():
    a, b = _two_admins()
    proj_b = b.post("/api/projects", json={"name": "PB"}).json()["data"]["id"]
    build_b = b.post(
        "/api/builds", json={"name": "BB", "project_id": proj_b, "quantity": 1}
    ).json()["data"]["id"]
    r = a.post(f"/api/builds/{build_b}/archive")
    assert r.status_code == 404, r.text


def test_build_archive_member_in_workspace_returns_403():
    owner, member = _admin_and_member(role="member")
    proj = owner.post("/api/projects", json={"name": "P"}).json()["data"]["id"]
    build = owner.post(
        "/api/builds", json={"name": "B", "project_id": proj, "quantity": 1}
    ).json()["data"]["id"]
    r = member.post(f"/api/builds/{build}/archive")
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------


def test_storage_archive_foreign_workspace_returns_404():
    a, b = _two_admins()
    storage_b = b.post("/api/storage", json={"name": "B-bin"}).json()["data"]["id"]
    r = a.post(f"/api/storage/{storage_b}/archive")
    assert r.status_code == 404, r.text


def test_storage_restore_member_in_workspace_returns_403():
    owner, member = _admin_and_member(role="member")
    storage = owner.post("/api/storage", json={"name": "Bin"}).json()["data"]["id"]
    owner.post(f"/api/storage/{storage}/archive")
    r = member.post(f"/api/storage/{storage}/restore")
    assert r.status_code == 403, r.text
