from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.api.routes.auth as auth_routes
from app.core.advisory_locks import PASSWORD_RESET_THROTTLE_LOCK_CLASSID
from app.core.auth import hmac_token
from app.core.time import utcnow
from app.domain.audit.models import AuditLog
from app.domain.users.models import PasswordResetRequest, User, UserSession
from app.domain.workspaces.models import WorkspaceMember
from app.main import app
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


@pytest.mark.real_db
def test_concurrent_same_token_serialises(client, db, monkeypatch):
    email = _signup(client)
    token = _request_reset(client, email)

    first_hash_entered = threading.Event()
    second_hash_entered = threading.Event()
    release_first_hash = threading.Event()
    hash_call_count = 0
    hash_call_lock = threading.Lock()
    original_hash_password = auth_routes.hash_password

    def _blocking_hash_password(password: str) -> str:
        nonlocal hash_call_count
        with hash_call_lock:
            hash_call_count += 1
            is_first_call = hash_call_count == 1
        if is_first_call:
            first_hash_entered.set()
            assert release_first_hash.wait(timeout=5)
        else:
            second_hash_entered.set()
        return original_hash_password(password)

    monkeypatch.setattr(auth_routes, "hash_password", _blocking_hash_password)

    results: list[tuple[int, str | None]] = []
    results_lock = threading.Lock()

    def _reset(new_password: str) -> None:
        with TestClient(app) as thread_client:
            response = thread_client.post(
                "/api/auth/reset-password",
                json={"token": token, "new_password": new_password},
            )
        with results_lock:
            results.append((response.status_code, response.json().get("code")))

    first = threading.Thread(target=_reset, args=(NEW_PASSWORD,), daemon=True)
    first.start()
    assert first_hash_entered.wait(timeout=5)

    second = threading.Thread(
        target=_reset,
        args=("AnotherPass-2026!!",),
        daemon=True,
    )
    second.start()

    assert not second_hash_entered.wait(timeout=0.5)
    release_first_hash.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert sorted(results) == [(200, None), (400, "auth.reset_used")]

    db.expire_all()
    assert db.query(PasswordResetRequest).one().used_at is not None
    reset_audits = (
        db.query(AuditLog)
        .filter(AuditLog.action == "user.password_reset")
        .all()
    )
    assert len(reset_audits) == 1


def test_password_reset_rejects_weak_password(client):
    email = _signup(client)
    token = _request_reset(client, email)

    response = client.post(
        "/api/auth/reset-password",
        json={"token": token, "new_password": "password123"},
    )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "auth.weak_password"


def test_archived_user_cannot_reset(client, db):
    email = _signup(client)
    token = _request_reset(client, email)

    user = db.query(User).filter(User.email == email).one()
    user.archived_at = utcnow()
    db.flush()

    response = client.post(
        "/api/auth/reset-password",
        json={"token": token, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "auth.reset_invalid"
    assert db.query(UserSession).count() == 1


def test_archived_user_cannot_request_reset_token(client, db):
    email = _signup(client)
    user = db.query(User).filter(User.email == email).one()
    user.archived_at = utcnow()
    db.flush()

    with patch("app.api.routes.auth.send_password_reset_email") as send_mail:
        response = client.post("/api/auth/request-password-reset", json={"email": email})

    assert response.status_code == 202, response.text
    assert response.json()["data"] == {"status": "accepted"}
    send_mail.assert_not_called()
    assert db.query(PasswordResetRequest).count() == 0


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


def test_throttle_rate_emits_distinct_audit_comment(client, db):
    email = _signup(client)

    with patch("app.api.routes.auth.send_password_reset_email") as send_mail:
        for _ in range(4):
            response = client.post(
                "/api/auth/request-password-reset",
                json={"email": email},
            )
            assert response.status_code == 202, response.text

    assert send_mail.call_count == 3
    comments = [
        row.comment
        for row in db.query(AuditLog)
        .filter(AuditLog.action == "user.password_reset_requested")
        .all()
    ]
    assert comments.count(None) == 3
    assert comments.count("throttled:rate") == 1
    assert "throttled:concurrent" not in comments


@pytest.mark.real_db
def test_throttle_atomic_under_concurrency(db, monkeypatch):
    email = f"reset-race-{uuid.uuid4().hex[:8]}@example.com"
    signup_user(TestClient(app), email=email)

    send_count = 0
    send_lock = threading.Lock()

    def _slow_send(*, to: str, reset_link: str) -> None:
        nonlocal send_count
        assert to == email
        assert "token=" in reset_link
        with send_lock:
            send_count += 1
        time.sleep(0.1)

    monkeypatch.setattr(auth_routes, "send_password_reset_email", _slow_send)

    thread_count = 6
    barrier = threading.Barrier(thread_count)
    results: list[int] = []
    errors: list[BaseException] = []
    results_lock = threading.Lock()

    def _request() -> None:
        try:
            client = TestClient(app)
            barrier.wait(timeout=10)
            response = client.post(
                "/api/auth/request-password-reset",
                json={"email": email},
            )
            with results_lock:
                results.append(response.status_code)
        except BaseException as exc:
            with results_lock:
                errors.append(exc)

    threads = [threading.Thread(target=_request) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert results == [202] * thread_count
    assert 1 <= send_count <= 3

    rows = (
        db.query(PasswordResetRequest)
        .filter_by(email_hash=auth_routes._hash_email_for_password_reset(email))
        .all()
    )
    assert len(rows) == thread_count
    assert sum(1 for row in rows if row.token_hmac is not None) == send_count


@pytest.mark.real_db
def test_throttle_lock_denied_returns_throttled(db, caplog):
    email = f"reset-lock-denied-{uuid.uuid4().hex[:8]}@example.com"
    signup_user(TestClient(app), email=email)
    email_hash = auth_routes._hash_email_for_password_reset(email)

    db.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "CAST(:classid AS int4), CAST(hashtext(:lock_key) AS int4)"
            ")"
        ),
        {
            "classid": PASSWORD_RESET_THROTTLE_LOCK_CLASSID,
            "lock_key": f"reset:{email_hash}",
        },
    )

    try:
        with (
            patch("app.api.routes.auth.send_password_reset_email") as send_mail,
            caplog.at_level(logging.INFO, logger=auth_routes.__name__),
        ):
            response = TestClient(app).post(
                "/api/auth/request-password-reset",
                json={"email": email},
            )
    finally:
        db.rollback()

    assert response.status_code == 202, response.text
    assert response.json()["data"] == {"status": "accepted"}
    send_mail.assert_not_called()
    assert "password_reset_request status=throttled reason=lock_denied" in caplog.text

    row = (
        db.query(PasswordResetRequest)
        .filter_by(email_hash=email_hash)
        .one()
    )
    assert row.token_hmac is None
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.action == "user.password_reset_requested")
        .one()
    )
    assert audit.comment == "throttled:concurrent"


def test_unknown_email_does_not_persist_row(client, db):
    with (
        patch("app.api.routes.auth.send_password_reset_email") as send_mail,
        patch("app.api.routes.auth.hash_password", return_value="dummy-hash") as hash_password,
    ):
        response = client.post(
            "/api/auth/request-password-reset",
            json={"email": f"unknown-reset-{uuid.uuid4().hex[:8]}@example.com"},
        )

    assert response.status_code == 202, response.text
    assert response.json()["data"] == {"status": "accepted"}
    send_mail.assert_not_called()
    hash_password.assert_called_once()
    assert db.query(PasswordResetRequest).count() == 0


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


def test_orphan_user_audit_behavior(client, db):
    email = _signup(client)
    user = db.query(User).filter(User.email == email).one()
    db.query(WorkspaceMember).filter(WorkspaceMember.user_id == user.id).delete()

    _request_reset(client, email)

    row = (
        db.query(AuditLog)
        .filter(AuditLog.action == "user.password_reset_requested")
        .one()
    )
    assert row.workspace_id is None
    assert row.user_id == user.id
    assert row.target_ids == [user.id]
