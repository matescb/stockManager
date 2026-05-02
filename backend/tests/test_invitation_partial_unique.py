"""Tests for the partial unique index uq_workspace_invitation_pending
(migration 0023, BE2-020 / #65).

Covers:
1. The index exists in the DB.
2. Creating a pending invitation, then accepting it, then creating
   another pending invite for the same email succeeds (the partial index
   only covers status='pending', so the accepted row doesn't block it).
3. Two pending invitations for the same (workspace, email) are rejected
   at the DB level.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

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
def admin():
    c = TestClient(app)
    _signup(c)
    return c


def test_partial_unique_index_exists():
    """Migration 0023 must have created uq_workspace_invitation_pending."""
    from app.infra.db import get_engine

    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT 1 FROM pg_indexes "
                "WHERE tablename = 'workspace_invitations' "
                "AND indexname = 'uq_workspace_invitation_pending'"
            )
        ).fetchall()
    assert rows, "uq_workspace_invitation_pending not present (migration 0023)"


def test_accept_then_reinvite_same_email_succeeds(admin):
    """Accept an invitation (status → 'accepted'), then invite the same
    email again. The partial index (WHERE status='pending') must allow
    a new pending row because the old row is no longer pending."""
    invitee_email = f"reinvite-{uuid.uuid4().hex[:6]}@x.com"

    # First invitation
    r1 = admin.post("/api/invitations", json={"email": invitee_email, "role": "member"})
    assert r1.status_code == 201, r1.text
    token1 = r1.json()["data"]["token"]

    # Invitee signs up and accepts
    invitee = TestClient(app)
    _signup(invitee, invitee_email)
    r_accept = invitee.post("/api/invitations/accept", json={"token": token1})
    assert r_accept.status_code == 200, r_accept.text

    # Admin invites the same email again — should succeed because the
    # previous row is now 'accepted', not 'pending'.
    # First, the existing member check will fire. To bypass: revoke path
    # is not needed here — we're testing the re-invite after accepting.
    # The API returns 409 if the user is already a member, so this test
    # checks the *DB constraint* rather than the API path.
    # Direct DB insert bypasses the already-member check:
    from app.domain.workspaces.models import WorkspaceInvitation
    from app.infra.db import SessionLocal

    ws_id = r1.json()["data"]["workspace_id"]

    with SessionLocal() as s:
        inv2 = WorkspaceInvitation(
            workspace_id=uuid.UUID(ws_id),
            email=invitee_email,
            role="member",
            token_hash="new-hash-" + uuid.uuid4().hex,
            status="pending",
        )
        s.add(inv2)
        # Should not raise — partial index only covers status='pending',
        # and the old row is 'accepted'.
        s.commit()


def test_duplicate_pending_same_email_rejected_at_db(admin):
    """Attempting to insert two pending rows for the same (workspace, email)
    directly at the DB level must fail with IntegrityError, proving that
    the unique index is in effect."""
    invitee_email = f"dupe-{uuid.uuid4().hex[:6]}@x.com"

    r = admin.post("/api/invitations", json={"email": invitee_email, "role": "member"})
    assert r.status_code == 201, r.text
    ws_id = r.json()["data"]["workspace_id"]

    from app.domain.workspaces.models import WorkspaceInvitation
    from app.infra.db import SessionLocal

    with SessionLocal() as s:
        inv2 = WorkspaceInvitation(
            workspace_id=uuid.UUID(ws_id),
            email=invitee_email,
            role="viewer",
            token_hash="conflict-hash-" + uuid.uuid4().hex,
            status="pending",
        )
        s.add(inv2)
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()
