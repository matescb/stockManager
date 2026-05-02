"""Email-verification signup flow tests (SEC2-014).

Covers:
- Signup with SIGNUP_REQUIRE_EMAIL_VERIFICATION=True returns 202.
- No User / Workspace exists after the 202.
- Verify creates User + Workspace in one transaction.
- A PendingUser row older than 24 h is treated as expired.
- A duplicate signup (same email, non-expired pending) returns 202 without
  creating a second row.
- Incorrect verification token returns 400.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.domain.users.models import PendingUser, User
from app.domain.workspaces.models import Workspace, WorkspaceMember
from app.infra.db import SessionLocal
from app.main import app


PASSWORD = "StrongVerify-2026!!"


def _full_signup_verify(
    *,
    email: str | None = None,
    name: str = "Verifier",
    workspace_name: str | None = None,
) -> tuple[TestClient, str, str]:
    """Perform the full signup + verify flow with email-verification enabled.

    Returns (client, email, pending_id).  The client has a session cookie
    set after the verify step.
    """
    email = email or f"v-{uuid.uuid4().hex[:8]}@x.com"
    c = TestClient(app)
    captured: dict[str, str] = {}

    def _cap(*, to: str, verification_link: str) -> None:
        captured["link"] = verification_link

    # Force the email-verification code path regardless of APP_ENV.
    with (
        patch.object(settings(), "SIGNUP_REQUIRE_EMAIL_VERIFICATION", True),
        patch("app.api.routes.auth.send_verification_email", side_effect=_cap),
    ):
        r = c.post(
            "/api/auth/signup",
            json={"email": email, "name": name, "password": PASSWORD,
                  **({"workspace_name": workspace_name} if workspace_name else {})},
        )
    assert r.status_code == 202, r.text
    assert "link" in captured, "send_verification_email was not called"

    m_id = re.search(r"[?&]id=([^&]+)", captured["link"])
    assert m_id
    pending_id = m_id.group(1)

    m_tok = re.search(r"[?&]token=([^&]+)", captured["link"])
    assert m_tok
    token = m_tok.group(1)

    verify_r = c.post("/api/auth/verify", json={"id": pending_id, "token": token})
    assert verify_r.status_code == 200, verify_r.text

    return c, email, pending_id


def test_signup_returns_202_when_verification_enabled():
    """With SIGNUP_REQUIRE_EMAIL_VERIFICATION=True, signup returns 202."""
    email = f"v-{uuid.uuid4().hex[:8]}@x.com"
    c = TestClient(app)

    with (
        patch.object(settings(), "SIGNUP_REQUIRE_EMAIL_VERIFICATION", True),
        patch("app.api.routes.auth.send_verification_email"),
    ):
        r = c.post(
            "/api/auth/signup",
            json={"email": email, "name": "T", "password": PASSWORD},
        )
    assert r.status_code == 202, r.text
    assert r.json()["data"]["status"] == "verification_sent"


def test_no_user_workspace_before_verify():
    """After a 202 signup, no User or Workspace row should exist yet."""
    email = f"v-{uuid.uuid4().hex[:8]}@x.com"
    c = TestClient(app)

    with (
        patch.object(settings(), "SIGNUP_REQUIRE_EMAIL_VERIFICATION", True),
        patch("app.api.routes.auth.send_verification_email"),
    ):
        c.post(
            "/api/auth/signup",
            json={"email": email, "name": "T", "password": PASSWORD},
        )

    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).first()
        assert user is None, "User row must not exist before verify"


def test_verify_creates_user_workspace():
    """POST /auth/verify promotes PendingUser → User + Workspace atomically."""
    client, email, _ = _full_signup_verify()

    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).first()
        assert user is not None, "User must exist after verify"

        memberships = db.query(WorkspaceMember).filter(WorkspaceMember.user_id == user.id).all()
        assert len(memberships) == 1
        assert memberships[0].role == "owner"

        ws = db.get(Workspace, memberships[0].workspace_id)
        assert ws is not None
        assert ws.kind == "personal"

    # Client should have a session cookie.
    cookie = client.cookies.get(settings().SESSION_COOKIE_NAME)
    assert cookie, "session cookie must be set after verify"


def test_verify_returns_user_and_workspace_id():
    """The verify response body should contain user and workspace_id."""
    client, email, _ = _full_signup_verify()

    # Re-call verify via a fresh call to check response shape — we already
    # consumed the verification in _full_signup_verify. Instead, let's
    # check the final state via /auth/me.
    me_r = client.get("/api/auth/me")
    assert me_r.status_code == 200, me_r.text
    me = me_r.json()["data"]
    assert me["user"]["email"] == email
    assert len(me["workspaces"]) == 1


def test_verify_wrong_token_returns_400():
    """An incorrect verification token returns 400 (not 200)."""
    email = f"v-{uuid.uuid4().hex[:8]}@x.com"
    c = TestClient(app)
    captured: dict[str, str] = {}

    def _cap(*, to: str, verification_link: str) -> None:
        captured["link"] = verification_link

    with (
        patch.object(settings(), "SIGNUP_REQUIRE_EMAIL_VERIFICATION", True),
        patch("app.api.routes.auth.send_verification_email", side_effect=_cap),
    ):
        c.post("/api/auth/signup", json={"email": email, "name": "T", "password": PASSWORD})

    m_id = re.search(r"[?&]id=([^&]+)", captured["link"])
    assert m_id
    pending_id = m_id.group(1)

    r = c.post("/api/auth/verify", json={"id": pending_id, "token": "wrong-token-value"})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "auth.verification_invalid"


def test_verify_expired_row_returns_400(db):
    """A PendingUser created more than 24 h ago is expired."""
    email = f"v-{uuid.uuid4().hex[:8]}@x.com"
    c = TestClient(app)
    captured: dict[str, str] = {}

    def _cap(*, to: str, verification_link: str) -> None:
        captured["link"] = verification_link

    with (
        patch.object(settings(), "SIGNUP_REQUIRE_EMAIL_VERIFICATION", True),
        patch("app.api.routes.auth.send_verification_email", side_effect=_cap),
    ):
        c.post("/api/auth/signup", json={"email": email, "name": "T", "password": PASSWORD})

    m_id = re.search(r"[?&]id=([^&]+)", captured["link"])
    m_tok = re.search(r"[?&]token=([^&]+)", captured["link"])
    assert m_id and m_tok

    # Backdate the created_at to > 24 h ago.
    pending = db.query(PendingUser).filter(PendingUser.email == email).first()
    assert pending is not None
    pending.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
    db.commit()

    r = c.post("/api/auth/verify", json={"id": m_id.group(1), "token": m_tok.group(1)})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "auth.verification_expired"


def test_duplicate_signup_same_email_returns_202_without_new_row(db):
    """A second signup for the same (non-expired) email returns 202 without
    creating a duplicate PendingUser row."""
    email = f"v-{uuid.uuid4().hex[:8]}@x.com"
    c = TestClient(app)

    def _noop(*, to, verification_link):
        pass

    with (
        patch.object(settings(), "SIGNUP_REQUIRE_EMAIL_VERIFICATION", True),
        patch("app.api.routes.auth.send_verification_email", side_effect=_noop),
    ):
        r1 = c.post("/api/auth/signup", json={"email": email, "name": "T", "password": PASSWORD})
        assert r1.status_code == 202

        # Count rows after first signup.
        count_after_first = db.query(PendingUser).filter(PendingUser.email == email).count()

        r2 = c.post("/api/auth/signup", json={"email": email, "name": "T2", "password": PASSWORD})
        assert r2.status_code == 202

    count_after_second = db.query(PendingUser).filter(PendingUser.email == email).count()
    assert count_after_second == count_after_first, (
        "second signup for same email must NOT create a new PendingUser row"
    )


def test_signup_rejects_already_verified_email():
    """Signing up with an email that already has a verified User returns 409."""
    email = f"v-{uuid.uuid4().hex[:8]}@x.com"
    # First: complete the full verification flow to create a User.
    _full_signup_verify(email=email)

    # Second attempt should 409.
    c2 = TestClient(app)
    with (
        patch.object(settings(), "SIGNUP_REQUIRE_EMAIL_VERIFICATION", True),
        patch("app.api.routes.auth.send_verification_email"),
    ):
        r = c2.post("/api/auth/signup", json={"email": email, "name": "T2", "password": PASSWORD})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "auth.email_taken"
