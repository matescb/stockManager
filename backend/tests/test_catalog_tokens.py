"""Tests for multi-token catalog access (SEC2-019 / issue #77).

Covers:
- Create token → list shows it → revoked_at is null
- Revoke token → catalog endpoint returns 404 for revoked token
- last_used_at updates on catalog hit
- Two active tokens both work; revoking one leaves the other alive
- Admin gating: member role gets 403 on create/list/revoke
- Workspace isolation: workspace A admin cannot revoke workspace B's token (404)
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests._factories import DEFAULT_PASSWORD, signup_user
from app.main import app


def _make_client() -> TestClient:
    return TestClient(app)


def _signup_admin(c: TestClient) -> str:
    """Sign up a new user and return their workspace_id."""
    r = signup_user(c)
    return r.json()["data"]["workspace_id"]


def _enable_catalog(c: TestClient) -> None:
    r = c.patch("/api/workspaces/current", json={"catalog_enabled": True})
    assert r.status_code == 200, r.text


def _create_token(c: TestClient, label: str = "test token") -> dict:
    r = c.post("/api/workspaces/current/catalog/tokens", json={"label": label})
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _list_tokens(c: TestClient) -> list:
    r = c.get("/api/workspaces/current/catalog/tokens")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _revoke_token(c: TestClient, token_id: str) -> int:
    r = c.delete(f"/api/workspaces/current/catalog/tokens/{token_id}")
    return r.status_code


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------


def test_create_token_appears_in_list():
    c = _make_client()
    _signup_admin(c)
    _enable_catalog(c)

    token_data = _create_token(c, label="my-token")

    assert "token" in token_data, "plaintext token must be returned on creation"
    assert token_data["label"] == "my-token"
    assert token_data["revoked_at"] is None
    assert token_data["id"] is not None

    tokens = _list_tokens(c)
    ids = [t["id"] for t in tokens]
    assert token_data["id"] in ids


def test_token_plaintext_not_in_list():
    """List endpoint must never return token_hmac or plaintext."""
    c = _make_client()
    _signup_admin(c)
    _enable_catalog(c)
    _create_token(c, label="secret-token")

    tokens = _list_tokens(c)
    for t in tokens:
        assert "token" not in t, "list must not return plaintext token"
        assert "token_hmac" not in t, "list must never return token_hmac"


def test_revoke_token():
    c = _make_client()
    _signup_admin(c)
    _enable_catalog(c)

    token_data = _create_token(c, label="to-revoke")
    token_id = token_data["id"]

    status_code = _revoke_token(c, token_id)
    assert status_code == 200, "revoke should return 200"

    # After revocation, the token row should have revoked_at set.
    tokens = _list_tokens(c)
    revoked = next((t for t in tokens if t["id"] == token_id), None)
    assert revoked is not None
    assert revoked["revoked_at"] is not None, "revoked_at must be set after revocation"


def test_revoked_token_cannot_access_catalog():
    c = _make_client()
    _signup_admin(c)
    _enable_catalog(c)

    token_data = _create_token(c, label="soon-revoked")
    plaintext = token_data["token"]

    # Should work before revocation.
    r = c.get(f"/catalog/{plaintext}")
    assert r.status_code == 200, "token should work before revocation"

    _revoke_token(c, token_data["id"])

    # Should 404 after revocation.
    r = c.get(f"/catalog/{plaintext}")
    assert r.status_code == 404, "revoked token must not grant catalog access"


def test_last_used_at_updates_on_catalog_hit():
    c = _make_client()
    _signup_admin(c)
    _enable_catalog(c)

    token_data = _create_token(c, label="used-token")
    plaintext = token_data["token"]
    token_id = token_data["id"]

    # last_used_at starts as null.
    tokens = _list_tokens(c)
    t = next(x for x in tokens if x["id"] == token_id)
    assert t["last_used_at"] is None, "last_used_at should start null"

    # Hit the catalog endpoint.
    r = c.get(f"/catalog/{plaintext}")
    assert r.status_code == 200

    # last_used_at should now be set.
    tokens = _list_tokens(c)
    t = next(x for x in tokens if x["id"] == token_id)
    assert t["last_used_at"] is not None, "last_used_at should update after catalog hit"


def test_two_active_tokens_both_work():
    c = _make_client()
    _signup_admin(c)
    _enable_catalog(c)

    t1 = _create_token(c, label="token-1")
    t2 = _create_token(c, label="token-2")

    # Both should work.
    r1 = c.get(f"/catalog/{t1['token']}")
    r2 = c.get(f"/catalog/{t2['token']}")
    assert r1.status_code == 200, "token 1 should work"
    assert r2.status_code == 200, "token 2 should work"

    # Revoke token-1.
    _revoke_token(c, t1["id"])

    # Token-2 still works; token-1 does not.
    r1_after = c.get(f"/catalog/{t1['token']}")
    r2_after = c.get(f"/catalog/{t2['token']}")
    assert r1_after.status_code == 404, "revoked token-1 must not work"
    assert r2_after.status_code == 200, "active token-2 must still work"


# ---------------------------------------------------------------------------
# RBAC gating
# ---------------------------------------------------------------------------


def _signup_member(c: TestClient, admin_c: TestClient, ws_id: str) -> TestClient:
    """Invite + sign up a member-role user in the given workspace, return their client."""
    email = f"member-{uuid.uuid4().hex[:6]}@example.com"
    # Invite as member.
    r = admin_c.post("/api/invitations", json={"email": email, "role": "member"})
    assert r.status_code == 201, r.text
    # composite token = "{invitation_id}:{plaintext}" (SEC2-013)
    inv_token = r.json()["data"]["token"]

    # Sign up and accept invitation.
    mc = _make_client()
    signup_user(mc, email=email)
    r2 = mc.post("/api/invitations/accept", json={"token": inv_token})
    assert r2.status_code == 200, r2.text

    # Switch to the correct workspace.
    mc.post(f"/api/workspaces/{ws_id}/switch")
    return mc


def test_member_cannot_list_tokens():
    admin_c = _make_client()
    ws_id = _signup_admin(admin_c)
    _enable_catalog(admin_c)

    mc = _signup_member(_make_client(), admin_c, ws_id)

    r = mc.get("/api/workspaces/current/catalog/tokens")
    assert r.status_code == 403, "member must not list tokens"


def test_member_cannot_create_token():
    admin_c = _make_client()
    ws_id = _signup_admin(admin_c)
    _enable_catalog(admin_c)

    mc = _signup_member(_make_client(), admin_c, ws_id)

    r = mc.post(
        "/api/workspaces/current/catalog/tokens",
        json={"label": "sneaky"},
    )
    assert r.status_code == 403, "member must not create tokens"


def test_member_cannot_revoke_token():
    admin_c = _make_client()
    ws_id = _signup_admin(admin_c)
    _enable_catalog(admin_c)

    token_data = _create_token(admin_c, label="admin-token")

    mc = _signup_member(_make_client(), admin_c, ws_id)
    r = mc.delete(f"/api/workspaces/current/catalog/tokens/{token_data['id']}")
    assert r.status_code == 403, "member must not revoke tokens"


# ---------------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------------


def test_workspace_a_admin_cannot_revoke_workspace_b_token():
    """Cross-workspace revocation attempt must return 404, not 403."""
    c_a = _make_client()
    _signup_admin(c_a)
    _enable_catalog(c_a)
    t_a = _create_token(c_a, label="ws-a-token")

    c_b = _make_client()
    _signup_admin(c_b)
    _enable_catalog(c_b)
    _create_token(c_b, label="ws-b-token")

    # Workspace A admin tries to revoke workspace B's token using token A's id.
    # (This verifies that workspace_id check produces 404 not 403.)
    # But we need workspace A's admin to try to revoke a token from workspace B.
    # Get workspace B's token id:
    tokens_b = _list_tokens(c_b)
    token_b_id = tokens_b[0]["id"]

    # Workspace A admin: their /current points to ws_a, so their DELETE
    # checks workspace_id == ws_a.id — token_b_id won't match → 404.
    r = c_a.delete(f"/api/workspaces/current/catalog/tokens/{token_b_id}")
    assert r.status_code == 404, "cross-workspace revocation must be 404 not 403"


def test_revoke_already_revoked_token_is_404():
    c = _make_client()
    _signup_admin(c)
    _enable_catalog(c)
    token_data = _create_token(c, label="double-revoke")

    _revoke_token(c, token_data["id"])
    status_code = _revoke_token(c, token_data["id"])
    assert status_code == 404, "double-revoke must return 404"


# ---------------------------------------------------------------------------
# Regression: legacy Workspace.catalog_token_hash must NOT bypass revocation
# ---------------------------------------------------------------------------


def test_legacy_catalog_token_hash_does_not_bypass_revocation():
    """SEC2-019 regression / sweep #51 finding.

    Before the fix, _resolve_workspace fell back to the legacy
    Workspace.catalog_token_hash column when the WorkspaceCatalogToken
    child-table lookup missed. That fallback did NOT enforce the
    `revoked_at IS NULL` predicate (the legacy column has no such field),
    so any token whose HMAC was mirrored into the legacy column kept
    authenticating even after the new-table row was revoked.

    This test reconstructs that bypass scenario: it creates a token via
    the new-table API, copies its HMAC into the legacy column to emulate
    a pre-migration workspace, revokes the new-table row, and asserts
    the catalog endpoint returns 404 — i.e. the legacy fallback is
    closed.
    """
    import uuid as _uuid

    from app.domain.workspaces.models import Workspace
    from app.infra.db import SessionLocal

    c = _make_client()
    ws_id = _signup_admin(c)
    _enable_catalog(c)

    token_data = _create_token(c, label="leaked-rotation")
    plaintext = token_data["token"]
    token_id = token_data["id"]

    # Sanity: the token works through the new-table path before revocation.
    r = c.get(f"/catalog/{plaintext}")
    assert r.status_code == 200, "token must work before revocation"

    # Mirror the new-table HMAC into the legacy Workspace.catalog_token_hash
    # column to emulate a workspace whose token predates migration 0032.
    # We re-derive the digest using the same _hmac_token the catalog router
    # uses, then write it directly via SessionLocal.
    from app.api.routes.catalog import _hmac_token

    digest = _hmac_token(plaintext)
    with SessionLocal() as s:
        ws = s.get(Workspace, _uuid.UUID(ws_id))
        assert ws is not None
        ws.catalog_token_hash = digest
        s.commit()

    # Revoke via the new-table endpoint — the new-table row's revoked_at
    # is now set, but the legacy column on Workspace still carries the
    # matching HMAC.
    assert _revoke_token(c, token_id) == 200

    # The bypass guard: the catalog endpoint MUST refuse this plaintext
    # token even though it would still match the legacy column. If the
    # legacy fallback is ever re-introduced without a revocation predicate,
    # this assertion fails.
    r = c.get(f"/catalog/{plaintext}")
    assert r.status_code == 404, (
        "revoked token must be rejected; legacy catalog_token_hash "
        "fallback would have authenticated it"
    )

    r = c.get(f"/catalog/{plaintext}/parts.json")
    assert r.status_code == 404, (
        "revoked token must be rejected on JSON endpoint too"
    )
