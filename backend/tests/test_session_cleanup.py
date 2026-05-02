"""Tests for `purge_expired_sessions` and the matching expires_at index
(DB-007 / issue #98).
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


def _make_session(db, *, user_id, expires_at: datetime) -> UserSession:
    token = uuid.uuid4().hex
    row = UserSession(
        token_hash=hash_session_token(token),
        user_id=user_id,
        expires_at=expires_at,
    )
    db.add(row)
    db.flush()
    return row


def test_purge_expired_sessions_drops_only_past_rows(db):
    user = _make_user(db)
    past = _make_session(db, user_id=user.id, expires_at=_utcnow() - timedelta(hours=1))
    future = _make_session(db, user_id=user.id, expires_at=_utcnow() + timedelta(hours=1))

    deleted = purge_expired_sessions(db)
    db.commit()

    assert deleted == 1
    remaining_hashes = {
        row.token_hash for row in db.query(UserSession).all()
    }
    assert past.token_hash not in remaining_hashes
    assert future.token_hash in remaining_hashes


def test_purge_expired_sessions_is_idempotent(db):
    user = _make_user(db)
    _make_session(db, user_id=user.id, expires_at=_utcnow() - timedelta(minutes=5))

    first = purge_expired_sessions(db)
    db.commit()
    second = purge_expired_sessions(db)
    db.commit()

    assert first == 1
    assert second == 0


def test_user_sessions_expires_at_index_exists(db):
    """The matching index from migration 0019 must exist — without it
    the purge query is a seq scan."""
    insp = inspect(db.get_bind())
    index_names = {ix["name"] for ix in insp.get_indexes("user_sessions")}
    assert "ix_user_sessions_expires_at" in index_names
