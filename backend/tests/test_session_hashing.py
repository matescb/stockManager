"""SEC2-003 + SEC2-015 — session token hashing + sliding expiry.

The DB only ever holds the SHA-256 digest of the cookie; the plaintext
lives only on the client cookie. A session idle past
core/deps._SESSION_IDLE_WINDOW (24h) is rejected even when its
absolute `expires_at` is still in the future.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.core.config import settings
from app.domain.users.models import UserSession
from app.main import app


def _signup(c: TestClient) -> str:
    email = f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text
    return email


def test_db_stores_hash_not_plaintext(db):
    c = TestClient(app)
    _signup(c)
    cookie = c.cookies.get(settings().SESSION_COOKIE_NAME)
    assert cookie, "expected the session cookie to be set on the client"

    rows = db.query(UserSession).all()
    assert len(rows) == 1
    row = rows[0]

    # The persisted row carries the digest, not the plaintext.
    expected = hashlib.sha256(cookie.encode("utf-8")).hexdigest()
    assert row.token_hash == expected
    # No `token` column survives the 0017 migration.
    assert not hasattr(row, "token") or getattr(row, "token", None) is None


def test_cookie_token_authenticates_against_hashed_row(db):
    c = TestClient(app)
    _signup(c)
    # /me requires auth; if the lookup-by-hash works, this 200s.
    r = c.get("/api/auth/me")
    assert r.status_code == 200, r.text


def test_login_rotates_existing_sessions(db):
    """SEC2-015 — old session for the same user is dropped on login."""
    email = f"u-{uuid.uuid4().hex[:8]}@x.com"
    pw = "TestPass-2026-Stronk"
    first = TestClient(app)
    r = first.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": pw},
    )
    assert r.status_code == 200, r.text
    old_cookie = first.cookies.get(settings().SESSION_COOKIE_NAME)
    assert old_cookie

    # Second client logs the same user in. The old row should be revoked.
    second = TestClient(app)
    r = second.post("/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    new_cookie = second.cookies.get(settings().SESSION_COOKIE_NAME)
    assert new_cookie and new_cookie != old_cookie

    # The first client's old cookie no longer authenticates.
    first.cookies.clear()
    first.cookies.set(settings().SESSION_COOKIE_NAME, old_cookie)
    r = first.get("/api/auth/me")
    assert r.status_code == 401, r.text


def test_idle_session_past_24h_rejected(db):
    """SEC2-015 — sliding expiry. Force `last_used_at` into the past
    and verify the next call gets a 401."""
    c = TestClient(app)
    _signup(c)
    # Push every session row's last_used_at into the past.
    rows = db.query(UserSession).all()
    assert rows
    for row in rows:
        row.last_used_at = datetime.now(timezone.utc) - timedelta(hours=25)
    db.commit()

    r = c.get("/api/auth/me")
    assert r.status_code == 401, r.text


def test_logout_drops_hashed_row(db):
    c = TestClient(app)
    _signup(c)
    assert db.query(UserSession).count() == 1
    r = c.post("/api/auth/logout")
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.query(UserSession).count() == 0
