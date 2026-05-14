from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.core.auth import hash_session_token, revoke_session
from app.core.config import settings
from app.domain.audit.models import AuditLog
from app.domain.users.models import UserSession
from tests._factories import signup_user


def test_logout_commits_session_revocation(client, db, monkeypatch):
    signup_user(client)
    token = client.cookies.get(settings().SESSION_COOKIE_NAME)
    assert token
    digest = hash_session_token(token)
    assert db.get(UserSession, digest) is not None

    commit_count = 0
    original_commit = db.commit

    def commit_spy():
        nonlocal commit_count
        commit_count += 1
        return original_commit()

    monkeypatch.setattr(db, "commit", commit_spy)

    revoke_session(db, token)

    assert commit_count == 1
    assert db.get(UserSession, digest) is None


def test_logout_writes_audit_row(client, db):
    signup = signup_user(client)
    signed_up = signup.json()["data"]
    user_id = UUID(signed_up["user"]["id"])
    workspace_id = UUID(signed_up["workspace_id"])

    request_id = "abc123"
    response = client.post("/api/auth/logout", headers={"X-Request-Id": request_id})
    assert response.status_code == 200, response.text

    rows = db.scalars(select(AuditLog).where(AuditLog.action == "user.logout")).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.workspace_id == workspace_id
    assert row.user_id == user_id
    assert row.target_type == "user"
    assert row.target_ids == [user_id]
    assert row.request_id == request_id
