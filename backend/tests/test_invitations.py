from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup(c, email=None):
    email = email or f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": "password123"},
    )
    assert r.status_code == 200, r.text
    return email, r.json()["data"]["workspace_id"]


@pytest.fixture
def admin():
    c = TestClient(app)
    _signup(c)
    return c


def test_signup_user_is_owner(admin):
    members = admin.get("/api/workspaces/members").json()["data"]
    assert len(members) == 1
    assert members[0]["role"] == "owner"


def test_admin_creates_invitation_user_accepts(admin):
    invitee_email = f"new-{uuid.uuid4().hex[:6]}@x.com"
    r = admin.post("/api/invitations", json={"email": invitee_email, "role": "member"})
    assert r.status_code == 201, r.text
    inv = r.json()["data"]
    assert inv["status"] == "pending"
    token = inv["token"]
    assert token

    # Sign up the invitee in their own client
    invitee = TestClient(app)
    invitee.post(
        "/api/auth/signup",
        json={"email": invitee_email, "name": "Newbie", "password": "password123"},
    )

    # Accept
    r = invitee.post("/api/invitations/accept", json={"token": token})
    assert r.status_code == 200, r.text
    accepted_ws = r.json()["data"]["workspace_id"]

    # Switch to the invited workspace and verify the user is now a member
    invitee.post(f"/api/workspaces/{accepted_ws}/switch")
    me = invitee.get("/api/auth/me").json()["data"]
    assert any(w["id"] == accepted_ws for w in me["workspaces"])

    # Admin sees both members
    members = admin.get("/api/workspaces/members").json()["data"]
    assert len(members) == 2
    roles = sorted([m["role"] for m in members])
    assert roles == ["member", "owner"]


def test_non_admin_cannot_invite():
    # Owner creates workspace, invites a member; that member tries to invite again
    owner = TestClient(app)
    _signup(owner)
    invitee_email = f"member-{uuid.uuid4().hex[:6]}@x.com"
    inv = owner.post("/api/invitations", json={"email": invitee_email, "role": "member"}).json()["data"]

    invitee = TestClient(app)
    invitee.post(
        "/api/auth/signup",
        json={"email": invitee_email, "name": "M", "password": "password123"},
    )
    invitee.post("/api/invitations/accept", json={"token": inv["token"]})
    # Switch to the shared workspace
    members_in_admin = owner.get("/api/workspaces/members").json()["data"]
    shared_ws = members_in_admin[0]["user_id"]  # not actually used; cookie was set on signup
    # Force switch via cookie
    me = invitee.get("/api/auth/me").json()["data"]
    target_ws = next(w["id"] for w in me["workspaces"] if w["name"] != "Newbie's workspace") if False else owner.get("/api/workspaces/members").json()["data"]
    # simpler: extract from list_workspaces
    wss = invitee.get("/api/workspaces").json()["data"]
    shared = next(w for w in wss if w["id"] != me["workspaces"][0]["id"])
    invitee.post(f"/api/workspaces/{shared['id']}/switch")

    r = invitee.post("/api/invitations", json={"email": "x@x.com", "role": "member"})
    assert r.status_code == 403


def test_invitation_email_must_match(admin):
    inv_for = f"forA-{uuid.uuid4().hex[:6]}@x.com"
    inv = admin.post("/api/invitations", json={"email": inv_for, "role": "member"}).json()["data"]
    other = TestClient(app)
    other.post(
        "/api/auth/signup",
        json={"email": f"someone-else-{uuid.uuid4().hex[:6]}@x.com", "name": "Other", "password": "password123"},
    )
    r = other.post("/api/invitations/accept", json={"token": inv["token"]})
    assert r.status_code == 403


def test_revoke_invitation_blocks_acceptance(admin):
    invitee_email = f"r-{uuid.uuid4().hex[:6]}@x.com"
    inv = admin.post("/api/invitations", json={"email": invitee_email, "role": "member"}).json()["data"]
    r = admin.delete(f"/api/invitations/{inv['id']}")
    assert r.status_code == 200

    invitee = TestClient(app)
    invitee.post(
        "/api/auth/signup",
        json={"email": invitee_email, "name": "x", "password": "password123"},
    )
    r = invitee.post("/api/invitations/accept", json={"token": inv["token"]})
    assert r.status_code == 400


def test_cannot_demote_last_owner(admin):
    me = admin.get("/api/workspaces/members").json()["data"][0]
    r = admin.patch(f"/api/workspaces/members/{me['id']}", json={"role": "member"})
    assert r.status_code == 400
    assert "last owner" in r.json()["status"]["message"]


def test_cannot_already_member(admin):
    invitee_email = f"dup-{uuid.uuid4().hex[:6]}@x.com"
    inv = admin.post("/api/invitations", json={"email": invitee_email, "role": "member"}).json()["data"]
    invitee = TestClient(app)
    invitee.post(
        "/api/auth/signup",
        json={"email": invitee_email, "name": "x", "password": "password123"},
    )
    invitee.post("/api/invitations/accept", json={"token": inv["token"]})
    # Now try to invite the same email again — should 409
    r = admin.post("/api/invitations", json={"email": invitee_email, "role": "member"})
    assert r.status_code == 409
