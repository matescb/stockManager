from __future__ import annotations

import re
import uuid
from datetime import timedelta
from unittest.mock import patch

from app.core.auth import hmac_token
from app.core.time import utcnow
from app.domain.audit.models import AuditLog
from app.domain.users.models import PasswordResetRequest, User, UserSession
from tests._factories import DEFAULT_PASSWORD, signup_user

NEW_PASSWORD = "NewResetPass-2026!!"


def _signup(client, email: str | None = None) -> str:
    email = email or f"reset-{uuid.uuid4().hex[:8]}@example.com"
    signup_user(client, email=email)
    return email


def _request_reset(client, email: str) -> str:
    captured: dict[str, str] = {}

    def _capture(*, to: str, reset_link: str) -> None:
        assert to == email
        captured["link"] = reset_link

    with patch("app.api.routes.auth.send_password_reset_email", side_effect=_capture):
        response = client.post("/api/auth/request-password-reset", json={"email": email})

    assert response.status_code == 202, response.text
    assert response.json()["data"] == {"status": "accepted"}
    match = re.search(r"[?&]token=([^&]+)", captured["link"])
    assert match
    return match.group(1)


def test_password_reset_happy_path_revokes_sessions_and_audits(client, db):
    email = _signup(client)
    assert db.query(UserSession).count() == 1

    token = _request_reset(client, email)
    reset_row = db.query(PasswordResetRequest).one()
    assert reset_row.token_hmac == hmac_token(token)
    assert token not in str(reset_row.__dict__)

    response = client.post(
        "/api/auth/reset-password",
        json={"token": token, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "password_reset"
    assert db.query(UserSession).count() == 0
    assert reset_row.used_at is not None

    stale_me = client.get("/api/auth/me")
    assert stale_me.status_code == 401

    old_login = client.post(
        "/api/auth/login",
        json={"email": email, "password": DEFAULT_PASSWORD},
    )
    assert old_login.status_code == 401

    new_login = client.post(
        "/api/auth/login",
        json={"email": email, "password": NEW_PASSWORD},
    )
    assert new_login.status_code == 200, new_login.text

    actions = [row.action for row in db.query(AuditLog).order_by(AuditLog.created_at).all()]
    assert "user.password_reset_requested" in actions
    assert "user.password_reset" in actions


def test_password_reset_expired_token_returns_friendly_code(client, db):
    email = _signup(client)
    token = _request_reset(client, email)

    reset_row = db.query(PasswordResetRequest).one()
    reset_row.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()

    response = client.post(
        "/api/auth/reset-password",
        json={"token": token, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "auth.reset_expired"


def test_password_reset_token_is_single_use(client):
    email = _signup(client)
    token = _request_reset(client, email)

    first = client.post(
        "/api/auth/reset-password",
        json={"token": token, "new_password": NEW_PASSWORD},
    )
    assert first.status_code == 200, first.text

    second = client.post(
        "/api/auth/reset-password",
        json={"token": token, "new_password": "AnotherPass-2026!!"},
    )
    assert second.status_code == 400, second.text
    assert second.json()["code"] == "auth.reset_used"


def test_password_reset_rejects_weak_password(client):
    email = _signup(client)
    token = _request_reset(client, email)

    response = client.post(
        "/api/auth/reset-password",
        json={"token": token, "new_password": "password123"},
    )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "auth.weak_password"


def test_password_reset_email_throttle_suppresses_fourth_send(client, db):
    email = _signup(client)

    with patch("app.api.routes.auth.send_password_reset_email") as send_mail:
        for _ in range(4):
            response = client.post(
                "/api/auth/request-password-reset",
                json={"email": email},
            )
            assert response.status_code == 202, response.text

    assert send_mail.call_count == 3
    rows = db.query(PasswordResetRequest).order_by(PasswordResetRequest.created_at).all()
    assert len(rows) == 4
    assert rows[-1].token_hmac is None


def test_password_reset_request_audit_row(client, db):
    email = _signup(client)

    _request_reset(client, email)

    user = db.query(User).filter(User.email == email).one()
    row = (
        db.query(AuditLog)
        .filter(AuditLog.action == "user.password_reset_requested")
        .one()
    )
    assert row.user_id == user.id
    assert row.target_ids == [user.id]
