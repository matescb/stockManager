from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy.orm import Session

from app.core.config import settings

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


# Top weak passwords from public breach lists. Not exhaustive (HIBP
# k-anonymity API is the proper bar) but blocks the worst common
# patterns at zero ops cost. Add to this list as obvious gaps surface.
_WEAK_PASSWORDS = frozenset({
    "12345678", "123456789", "1234567890",
    "password", "password1", "password12", "password123",
    "qwerty12", "qwerty123", "qwertyuiop",
    "letmein", "letmein123",
    "iloveyou", "monkey123", "welcome1", "welcome123",
    "admin1234", "admin12345", "administrator",
    "1q2w3e4r", "1qaz2wsx", "zaq12wsx",
    "11111111", "00000000", "abcdefgh", "abc12345",
    "stockmgr", "stockmanager",
})


class WeakPasswordError(ValueError):
    """Raised when a candidate password fails the strength check."""


def validate_password_strength(password: str) -> None:
    """Reject obvious weak passwords. Caller maps to HTTPException 400.

    Stricter than the schema's `min_length=8` but kept loose by
    design — argon2 + slowapi rate-limit + the per-IP login cap are
    the load-bearing defences. This filter just stops "password123"
    from being committed to the DB.
    """
    if len(password) < 8:
        raise WeakPasswordError("password must be at least 8 characters")
    if password.lower() in _WEAK_PASSWORDS:
        raise WeakPasswordError(
            "password is on the public breach list — pick something else"
        )
    # Reject low-entropy patterns: same char repeated, or fewer than 4
    # distinct chars (covers "aaaaaaaa", "abababab", "12121212").
    if len(set(password)) < 4:
        raise WeakPasswordError(
            "password is too repetitive (use a longer / more varied passphrase)"
        )


def verify_password(hash_: str, password: str) -> bool:
    try:
        return _hasher.verify(hash_, password)
    except VerifyMismatchError:
        return False


def new_session_token() -> str:
    """Mint a fresh plaintext session token. Lives only on the client
    cookie; the server stores `hash_session_token(token)` in
    `user_sessions.token_hash` (SEC2-003)."""
    return secrets.token_urlsafe(48)


def hash_session_token(token: str) -> str:
    """SHA-256 hex digest of the cookie-side plaintext token. Used as
    the primary key on `user_sessions`, mirroring the invitation token
    hashing landed in PR #14."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=settings().SESSION_LIFETIME_DAYS)


@dataclass
class IssuedSession:
    """Return type for `create_session_row`: the plaintext token (which
    must be set on the cookie) plus the persisted row's hash. The model
    instance no longer carries the plaintext, so the route layer cannot
    accidentally log it."""

    token: str
    token_hash: str


def create_session_row(db: Session, user_id) -> IssuedSession:
    from app.domain.users.models import UserSession

    token = new_session_token()
    digest = hash_session_token(token)
    row = UserSession(token_hash=digest, user_id=user_id, expires_at=session_expires_at())
    db.add(row)
    db.flush()
    return IssuedSession(token=token, token_hash=digest)


def revoke_session(db: Session, token: str) -> None:
    """Delete the session row matching the plaintext cookie token, if
    any. Hashing is constant-time on input length; we don't need
    hmac.compare_digest because the lookup is by primary-key equality
    on a SHA-256 digest (pre-image resistant)."""
    from app.domain.users.models import UserSession

    digest = hash_session_token(token)
    row = db.query(UserSession).filter(UserSession.token_hash == digest).first()
    if row:
        db.delete(row)


def revoke_all_user_sessions(db: Session, user_id) -> int:
    """Delete every existing session for a user. Called on login so a
    fresh credential never coexists with a previously-issued one
    (SEC2-015 — session rotation on auth)."""
    from app.domain.users.models import UserSession

    rows = db.query(UserSession).filter(UserSession.user_id == user_id).all()
    for r in rows:
        db.delete(r)
    return len(rows)
