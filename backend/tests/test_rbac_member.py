"""TEST-002: RBAC matrix for the `member` role.

`test_rbac_viewer.py` covers the lowest tier (viewer = read-only).
Members should be able to do everything viewers can plus most write
operations, and be REJECTED from admin-gated endpoints (archive /
restore / bulk-delete on parts/orders/projects/storage/builds, plus
PATCH /api/workspaces/current for secrets).

The shape mirrors the viewer matrix so the two stay in lockstep.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient, email: str | None = None) -> tuple[str, str]:
    email = email or f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text
    return email, r.json()["data"]["workspace_id"]


@pytest.fixture
def owner_and_member():
    """Owner creates a workspace, invites a user as `member`, member
    accepts and switches into the shared workspace."""
    owner = TestClient(app)
    _, owner_ws_id = _signup(owner)

    invitee_email = f"member-{uuid.uuid4().hex[:6]}@x.com"
    inv = owner.post(
        "/api/invitations",
        json={"email": invitee_email, "role": "member"},
    ).json()["data"]
    assert inv["role"] == "member"
    token = inv["token"]
    assert token

    member = TestClient(app)
    member.post(
        "/api/auth/signup",
        json={"email": invitee_email, "name": "Mem", "password": "TestPass-2026-Stronk"},
    )
    member.post("/api/invitations/accept", json={"token": token})

    member.post(f"/api/workspaces/{owner_ws_id}/switch")

    return owner, member


# ---------------------------------------------------------------------------
# Member can do all the writes a viewer cannot. These mirror the
# viewer-cannot-* tests inverted.
# ---------------------------------------------------------------------------


def test_member_can_create_part(owner_and_member):
    _, member = owner_and_member
    r = member.post("/api/parts", json={"name": "MemPart", "part_type": "local"})
    assert r.status_code in (200, 201), r.text


def test_member_can_add_stock(owner_and_member):
    owner, member = owner_and_member
    pid = owner.post(
        "/api/parts", json={"name": "P", "part_type": "local"}
    ).json()["data"]["id"]
    r = member.post("/api/stock/add", json={"part_id": pid, "quantity": 5})
    assert r.status_code == 200, r.text


def test_member_can_create_order(owner_and_member):
    _, member = owner_and_member
    r = member.post("/api/orders", json={"name": "PO-1"})
    assert r.status_code in (200, 201), r.text


def test_member_can_create_build(owner_and_member):
    _, member = owner_and_member
    proj_id = member.post(
        "/api/projects", json={"name": "Proj"}
    ).json()["data"]["id"]
    r = member.post(
        "/api/builds",
        json={"name": "B", "project_id": proj_id, "quantity": 1},
    )
    assert r.status_code in (200, 201), r.text


def test_member_can_patch_project(owner_and_member):
    _, member = owner_and_member
    proj_id = member.post(
        "/api/projects", json={"name": "Proj"}
    ).json()["data"]["id"]
    r = member.patch(f"/api/projects/{proj_id}", json={"name": "renamed"})
    assert r.status_code == 200, r.text


def test_member_can_upload_attachment(owner_and_member):
    """Attachments router was missing from the viewer matrix too —
    cover both as a follow-on (see TEST-002)."""
    owner, member = owner_and_member
    pid = owner.post(
        "/api/parts", json={"name": "Attached", "part_type": "local"}
    ).json()["data"]["id"]
    files = {"file": ("a.png", b"\x89PNG\r\n\x1a\n0123456789", "image/png")}
    data = {"object_type": "part", "object_id": pid, "file_type": "other"}
    r = member.post("/api/attachments", data=data, files=files)
    # 201 on success; 415 if the magic-byte sniff rejects (the trailing
    # bytes here are not a real PNG body but the header is). Both are
    # member-allowed responses; what we care about is no 403.
    assert r.status_code != 403, r.text


def test_member_can_write_custom_field(owner_and_member):
    """Custom-fields router similarly missing from viewer matrix."""
    owner, member = owner_and_member
    pid = owner.post(
        "/api/parts", json={"name": "P-cf", "part_type": "local"}
    ).json()["data"]["id"]
    r = member.post(
        "/api/custom-fields",
        json={"object_type": "part", "object_id": pid, "key": "k", "value": "v"},
    )
    assert r.status_code != 403, r.text


# ---------------------------------------------------------------------------
# Members CANNOT do admin-gated operations: archive / restore / bulk-
# delete on parts/orders/projects/builds/storage, and the
# secrets-bearing PATCH on /api/workspaces/current.
# ---------------------------------------------------------------------------


def test_member_cannot_archive_part(owner_and_member):
    owner, member = owner_and_member
    pid = owner.post(
        "/api/parts", json={"name": "P", "part_type": "local"}
    ).json()["data"]["id"]
    r = member.post(f"/api/parts/{pid}/archive")
    assert r.status_code == 403, r.text


def test_member_cannot_restore_part(owner_and_member):
    owner, member = owner_and_member
    pid = owner.post(
        "/api/parts", json={"name": "P", "part_type": "local"}
    ).json()["data"]["id"]
    owner.post(f"/api/parts/{pid}/archive")
    r = member.post(f"/api/parts/{pid}/restore")
    assert r.status_code == 403, r.text


def test_member_cannot_bulk_delete_parts(owner_and_member):
    owner, member = owner_and_member
    pid = owner.post(
        "/api/parts", json={"name": "P", "part_type": "local"}
    ).json()["data"]["id"]
    r = member.post("/api/parts/bulk-delete", json={"part_ids": [pid]})
    assert r.status_code == 403, r.text


def test_member_cannot_archive_order(owner_and_member):
    owner, member = owner_and_member
    oid = owner.post("/api/orders", json={"name": "O"}).json()["data"]["id"]
    r = member.post(f"/api/orders/{oid}/archive")
    assert r.status_code == 403, r.text


def test_member_cannot_archive_project(owner_and_member):
    owner, member = owner_and_member
    pid = owner.post("/api/projects", json={"name": "P"}).json()["data"]["id"]
    r = member.post(f"/api/projects/{pid}/archive")
    assert r.status_code == 403, r.text


def test_member_cannot_archive_build(owner_and_member):
    owner, member = owner_and_member
    proj = owner.post("/api/projects", json={"name": "P"}).json()["data"]["id"]
    bid = owner.post(
        "/api/builds", json={"name": "B", "project_id": proj, "quantity": 1}
    ).json()["data"]["id"]
    r = member.post(f"/api/builds/{bid}/archive")
    assert r.status_code == 403, r.text


def test_member_cannot_archive_storage(owner_and_member):
    owner, member = owner_and_member
    sid = owner.post("/api/storage", json={"name": "S"}).json()["data"]["id"]
    r = member.post(f"/api/storage/{sid}/archive")
    assert r.status_code == 403, r.text


def test_member_cannot_patch_workspace_secrets(owner_and_member):
    """PATCH /api/workspaces/current rotates provider/scanner secrets
    — admin+ only. Member must not be able to pivot the workspace's
    integrations."""
    _, member = owner_and_member
    r = member.patch(
        "/api/workspaces/current",
        json={"parts_provider_api_key": "leaked"},
    )
    assert r.status_code == 403, r.text
