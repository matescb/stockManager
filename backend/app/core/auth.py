from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy.orm import Session

from app.core.config import settings

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hash_: str, password: str) -> bool:
    try:
        return _hasher.verify(hash_, password)
    except VerifyMismatchError:
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(48)


def session_expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=settings().SESSION_LIFETIME_DAYS)


def create_session_row(db: Session, user_id):
    from app.domain.users.models import UserSession

    token = new_session_token()
    row = UserSession(token=token, user_id=user_id, expires_at=session_expires_at())
    db.add(row)
    db.flush()
    return row


def revoke_session(db: Session, token: str) -> None:
    from app.domain.users.models import UserSession

    row = db.query(UserSession).filter(UserSession.token == token).first()
    if row:
        db.delete(row)
