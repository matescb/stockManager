from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import inspect

from app.core.auth import hmac_token, purge_password_reset_requests
from app.domain.users.models import PasswordResetRequest, User


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_user(db) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"reset-purge-{uuid.uuid4().hex[:8]}@example.com",
        name="Reset Purge",
        password_hash="$argon2id$dummy",
    )
    db.add(user)
    db.flush()
    return user


def _make_reset_request(
    db,
    *,
    user_id,
    created_at: datetime,
    token_seed: str,
    expires_at: datetime | None,
) -> PasswordResetRequest:
    row = PasswordResetRequest(
        id=uuid.uuid4(),
        user_id=user_id,
        email_hash="a" * 64,
        token_hmac=hmac_token(token_seed),
        created_at=created_at,
        expires_at=expires_at,
        ip="127.0.0.1",
        sent_at=created_at,
    )
    db.add(row)
    db.flush()
    return row


def test_purge_deletes_old_rows(db):
    user = _make_user(db)
    now = _utcnow()
    old_issued = _make_reset_request(
        db,
        user_id=user.id,
        created_at=now - timedelta(days=31),
        token_seed="old-issued",
        expires_at=now - timedelta(days=31) + timedelta(hours=1),
    )
    old_throttle = _make_reset_request(
        db,
        user_id=user.id,
        created_at=now - timedelta(days=31),
        token_seed="old-throttle",
        expires_at=None,
    )
    recent = _make_reset_request(
        db,
        user_id=user.id,
        created_at=now - timedelta(days=29),
        token_seed="recent",
        expires_at=now - timedelta(days=29) + timedelta(hours=1),
    )

    first = purge_password_reset_requests(db, now=now)
    db.commit()
    second = purge_password_reset_requests(db, now=now)
    db.commit()

    remaining_ids = {row.id for row in db.query(PasswordResetRequest).all()}
    assert first == 2
    assert second == 0
    assert old_issued.id not in remaining_ids
    assert old_throttle.id not in remaining_ids
    assert recent.id in remaining_ids


def test_password_reset_retention_uses_created_at_index(db):
    insp = inspect(db.get_bind())
    index_names = {ix["name"] for ix in insp.get_indexes("password_reset_requests")}

    assert "ix_password_reset_requests_created_at" in index_names
    assert "ix_password_reset_requests_expires_at" not in index_names
