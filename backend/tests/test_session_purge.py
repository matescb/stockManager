"""Tests for `purge_expired_sessions` and matching session purge indexes.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import inspect

from app.core.auth import hash_session_token, purge_expired_sessions
from app.domain.users.models import User, UserSession


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_user(db) -> User:
    u = User(
        id=uuid.uuid4(),
        email=f"sess-{uuid.uuid4().hex[:8]}@example.com",
        name="Test",
        password_hash="$argon2id$dummy",
    )
    db.add(u)
    db.flush()
    return u


def _make_session(
    db,
    *,
    user_id,
    expires_at: datetime,
    last_used_at: datetime | None = None,
) -> UserSession:
    token = uuid.uuid4().hex
    row = UserSession(
        token_hash=hash_session_token(token),
        user_id=user_id,
        expires_at=expires_at,
        last_used_at=last_used_at or _utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def test_purge_expired_sessions_drops_only_past_rows(db):
    user = _make_user(db)
    now = _utcnow()
    past = _make_session(db, user_id=user.id, expires_at=now - timedelta(hours=1))
    future = _make_session(db, user_id=user.id, expires_at=now + timedelta(hours=1))

    deleted = purge_expired_sessions(db, now=now)
    db.commit()

    assert deleted == 1
    remaining_hashes = {
        row.token_hash for row in db.query(UserSession).all()
    }
    assert past.token_hash not in remaining_hashes
    assert future.token_hash in remaining_hashes


def test_purge_removes_idle_rejected_rows(db):
    user = _make_user(db)
    now = _utcnow()
    idle_rejected = _make_session(
        db,
        user_id=user.id,
        expires_at=now + timedelta(days=10),
        last_used_at=now - timedelta(hours=25),
    )
    exactly_at_idle_window = _make_session(
        db,
        user_id=user.id,
        expires_at=now + timedelta(days=10),
        last_used_at=now - timedelta(hours=24),
    )
    active = _make_session(
        db,
        user_id=user.id,
        expires_at=now + timedelta(days=10),
        last_used_at=now - timedelta(hours=23),
    )

    deleted = purge_expired_sessions(db, now=now)
    db.commit()

    assert deleted == 1
    remaining_hashes = {
        row.token_hash for row in db.query(UserSession).all()
    }
    assert idle_rejected.token_hash not in remaining_hashes
    assert exactly_at_idle_window.token_hash in remaining_hashes
    assert active.token_hash in remaining_hashes


def test_purge_expired_sessions_is_idempotent(db):
    user = _make_user(db)
    now = _utcnow()
    _make_session(db, user_id=user.id, expires_at=now - timedelta(minutes=5))

    first = purge_expired_sessions(db, now=now)
    db.commit()
    second = purge_expired_sessions(db, now=now)
    db.commit()

    assert first == 1
    assert second == 0


def test_user_sessions_expires_at_index_exists(db):
    """The matching index from migration 0019 must exist — without it
    the purge query is a seq scan."""
    insp = inspect(db.get_bind())
    index_names = {ix["name"] for ix in insp.get_indexes("user_sessions")}
    assert "ix_user_sessions_expires_at" in index_names


def test_user_sessions_last_used_at_index_exists(db):
    """Idle-session purge needs a seekable last-used cutoff."""
    insp = inspect(db.get_bind())
    index_names = {ix["name"] for ix in insp.get_indexes("user_sessions")}
    assert "ix_user_sessions_last_used_at" in index_names
