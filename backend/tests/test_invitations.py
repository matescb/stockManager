from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup(c, email=None):
    email = email or f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": "TestPass-2026-Stronk"},
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
        json={"email": invitee_email, "name": "Newbie", "password": "TestPass-2026-Stronk"},
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
        json={"email": invitee_email, "name": "M", "password": "TestPass-2026-Stronk"},
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
        json={"email": f"someone-else-{uuid.uuid4().hex[:6]}@x.com", "name": "Other", "password": "TestPass-2026-Stronk"},
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
        json={"email": invitee_email, "name": "x", "password": "TestPass-2026-Stronk"},
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
        json={"email": invitee_email, "name": "x", "password": "TestPass-2026-Stronk"},
    )
    invitee.post("/api/invitations/accept", json={"token": inv["token"]})
    # Now try to invite the same email again — should 409
    r = admin.post("/api/invitations", json={"email": invitee_email, "role": "member"})
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Token-hashing-at-rest (PR #18 / Sec MED-7-style hardening)
# ---------------------------------------------------------------------------


def test_token_is_stored_as_hash_not_plaintext(admin):
    """The composite token returned to the caller must NOT appear in plaintext
    in the DB.  The DB has only the SHA-256 digest (token_hash) and the
    HMAC-SHA-256 digest (token_hmac).

    SEC2-013: the create response now returns a composite token of the form
    "{invitation_id}:{plaintext_token}" so the accept flow can look up by
    PK rather than by hash (no timing oracle).
    """
    import hashlib
    import hmac as _hmac

    invitee_email = f"hash-{uuid.uuid4().hex[:6]}@x.com"
    inv = admin.post(
        "/api/invitations", json={"email": invitee_email, "role": "member"}
    ).json()["data"]
    composite = inv["token"]
    assert composite, "create response must carry the composite token"

    # Split composite "{id}:{plaintext}" to recover the plaintext portion.
    inv_id_str, plaintext = composite.split(":", 1)
    assert inv_id_str == inv["id"], "composite token must be prefixed with the invitation id"

    # Reach into the DB and verify what landed.
    from app.core.config import settings
    from app.domain.workspaces.models import WorkspaceInvitation
    from app.infra.db import SessionLocal

    with SessionLocal() as s:
        row = s.get(WorkspaceInvitation, uuid.UUID(inv["id"]))
        assert row is not None
        # The plaintext is gone — the model only has token_hash and token_hmac.
        assert not hasattr(row, "token") or getattr(row, "token", None) is None
        assert row.token_hash == hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        # token_hash is NOT the plaintext.
        assert row.token_hash != plaintext
        # token_hmac is present and is the HMAC-SHA-256 of the plaintext.
        assert row.token_hmac is not None
        key = settings().SESSION_SECRET.encode("utf-8")
        expected_hmac = _hmac.new(key, plaintext.encode("utf-8"), "sha256").hexdigest()
        assert row.token_hmac == expected_hmac
        assert row.token_hmac != plaintext


def test_list_invitations_does_not_leak_token(admin):
    """List endpoint returns token=None for every row — the plaintext
    only ever exists in the create response. A re-fetch can't recover it."""
    invitee_email = f"list-{uuid.uuid4().hex[:6]}@x.com"
    admin.post("/api/invitations", json={"email": invitee_email, "role": "member"})

    rows = admin.get("/api/invitations").json()["data"]
    assert len(rows) >= 1
    for r in rows:
        if r["email"] == invitee_email:
            assert r["token"] is None, "list must not echo any token, plaintext or hash"


def test_accept_with_wrong_token_returns_404(admin):
    """SEC2-013: wrong-token scenarios all return 404.

    Three sub-cases are exercised:
    a) Composite token with correct id but wrong plaintext — HMAC mismatch.
    b) Composite token with a non-existent id.
    c) Malformed token (no colon separator) — rejected before DB lookup.
    """
    invitee_email = f"wrong-{uuid.uuid4().hex[:6]}@x.com"
    inv_data = admin.post(
        "/api/invitations", json={"email": invitee_email, "role": "member"}
    ).json()["data"]
    inv_id = inv_data["id"]

    invitee = TestClient(app)
    invitee.post(
        "/api/auth/signup",
        json={"email": invitee_email, "name": "x", "password": "TestPass-2026-Stronk"},
    )

    # a) Correct id, wrong plaintext — HMAC compare_digest must fail → 404.
    r = invitee.post(
        "/api/invitations/accept",
        json={"token": f"{inv_id}:WRONG-PLAINTEXT-VALUE"},
    )
    assert r.status_code == 404, r.text

    # b) Non-existent id — DB lookup returns None → 404.
    fake_id = str(uuid.uuid4())
    r = invitee.post(
        "/api/invitations/accept",
        json={"token": f"{fake_id}:some-plaintext"},
    )
    assert r.status_code == 404, r.text

    # c) Malformed token (no colon) — format validation → 404.
    r = invitee.post("/api/invitations/accept", json={"token": "NO-COLON-TOKEN"})
    assert r.status_code == 404, r.text


def test_accept_endpoint_has_rate_limit_decorator():
    """slowapi is disabled outside prod so we can't trip the limit at
    runtime in tests. Pin the decorator's presence — if a future
    refactor drops the @limiter.limit, this test fails."""
    from app.api.routes.invitations import accept_invitation

    markers = ("limiter_kwargs", "_limiter", "limit", "__wrapped__")
    has_marker = any(hasattr(accept_invitation, m) for m in markers)
    assert has_marker, (
        f"expected @limiter.limit on accept_invitation; markers checked: {markers}"
    )
