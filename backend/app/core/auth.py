from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utcnow

_log = logging.getLogger(__name__)

_hasher = PasswordHasher()


@dataclass(frozen=True)
class PasswordVerifyResult:
    valid: bool
    rehash: str | None = None


def _password_material(password: str) -> str:
    pepper = settings().PASSWORD_PEPPER
    if not pepper:
        return password
    return hmac.new(
        pepper.encode("utf-8"),
        password.encode("utf-8"),
        "sha256",
    ).hexdigest()


def hash_password(password: str) -> str:
    return _hasher.hash(_password_material(password))


def verify_password_with_rehash(hash_: str, password: str) -> PasswordVerifyResult:
    material = _password_material(password)
    try:
        if _hasher.verify(hash_, material):
            rehash = _hasher.hash(material) if _hasher.check_needs_rehash(hash_) else None
            return PasswordVerifyResult(valid=True, rehash=rehash)
    except (VerificationError, InvalidHashError):
        pass

    if material == password:
        return PasswordVerifyResult(valid=False)

    try:
        if _hasher.verify(hash_, password):
            return PasswordVerifyResult(valid=True, rehash=hash_password(password))
    except (VerificationError, InvalidHashError):
        pass

    return PasswordVerifyResult(valid=False)


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


def _weak_passwords() -> frozenset[str]:
    return _WEAK_PASSWORDS | frozenset(
        password.lower() for password in settings().EXTRA_WEAK_PASSWORDS
    )


class WeakPasswordError(ValueError):
    """Raised when a candidate password fails the strength check."""


def _hibp_check(password: str) -> None:
    """Query the HIBP k-anonymity range API for ``password``.

    Computes SHA-1(password), sends the first 5 hex chars to
    https://api.pwnedpasswords.com/range/{prefix}, then scans the
    response for the remaining 35-char suffix.

    Raises WeakPasswordError if the password appears in the breach list.

    FAIL-OPEN: any HTTP error, timeout, or unexpected response is logged
    as a warning and the function returns without raising.  Blocking ALL
    signups when HIBP is unreachable is worse than the modest signal lost
    on a brief outage.  Sentry will capture the warning so ops can see
    the rate of fall-throughs.
    """
    sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()  # noqa: S324
    prefix, suffix = sha1[:5], sha1[5:]
    try:
        resp = httpx.get(
            f"https://api.pwnedpasswords.com/range/{prefix}",
            timeout=2.0,
            headers={"Add-Padding": "true"},
        )
        resp.raise_for_status()
    except Exception as exc:
        # Fail-open: HIBP down / timeout / network error.  Document the
        # event for ops via Sentry breadcrumb (if Sentry is configured)
        # and a log warning.  The caller proceeds without raising.
        _log.warning("HIBP check failed (fail-open): %s", exc)
        try:
            import sentry_sdk
            sentry_sdk.add_breadcrumb(
                category="hibp",
                message=f"HIBP range API unavailable — fail-open: {exc}",
                level="warning",
            )
        except ImportError:
            pass
        return
    for line in resp.text.splitlines():
        parts = line.strip().split(":")
        if len(parts) == 2 and parts[0].upper() == suffix:
            count = int(parts[1]) if parts[1].isdigit() else 1
            if count > 0:
                raise WeakPasswordError(
                    f"password has appeared in {count:,} known data breaches — "
                    "choose a unique password"
                )


def validate_password_strength(password: str) -> None:
    """Reject obvious weak passwords. Caller maps to HTTPException 400.

    Stricter than the schema's `min_length=8` but kept loose by
    design — argon2 + slowapi rate-limit + the per-account lockout are
    the load-bearing defences. This filter stops "password123" from
    being committed to the DB and also checks against the HIBP k-anonymity
    breach corpus.

    HIBP check is fail-open (see _hibp_check docstring): a network
    failure falls back to the local blocklist + entropy heuristic so
    signups aren't blocked when HIBP is unreachable.
    """
    if len(password) < 8:
        raise WeakPasswordError("password must be at least 8 characters")
    if password.lower() in _weak_passwords():
        raise WeakPasswordError(
            "password is on the public breach list — pick something else"
        )
    # Reject low-entropy patterns: same char repeated, or fewer than 4
    # distinct chars (covers "aaaaaaaa", "abababab", "12121212").
    if len(set(password)) < 4:
        raise WeakPasswordError(
            "password is too repetitive (use a longer / more varied passphrase)"
        )
    # HIBP k-anonymity check — fail-open on network error.
    _hibp_check(password)


def verify_password(hash_: str, password: str) -> bool:
    return verify_password_with_rehash(hash_, password).valid


def hmac_token(token: str) -> str:
    """HMAC-SHA-256 hex digest for bearer tokens stored at rest.

    Used by email verification and password reset flows so database
    dumps cannot replay raw email-link credentials.
    """
    key = settings().SESSION_SECRET.encode("utf-8")
    return hmac.new(key, token.encode("utf-8"), "sha256").hexdigest()


def mint_password_reset_token() -> tuple[str, str]:
    """Return (plaintext token, HMAC digest) for a password reset link."""
    token = secrets.token_urlsafe(48)
    return token, hmac_token(token)


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
    return utcnow() + timedelta(days=settings().SESSION_LIFETIME_DAYS)


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
        db.commit()


def revoke_all_user_sessions(db: Session, user_id) -> int:
    """Delete every existing session for a user. Called on login so a
    fresh credential never coexists with a previously-issued one
    (SEC2-015 — session rotation on auth)."""
    from app.domain.users.models import UserSession

    rows = db.query(UserSession).filter(UserSession.user_id == user_id).all()
    for r in rows:
        db.delete(r)
    return len(rows)


# ---------------------------------------------------------------------------
# Per-account login lockout (SEC2-014).
#
# Thresholds are hardcoded — no config knob yet.  Change here if the ops
# team decides to tune them; no migration is needed (these are pure code
# constants, not DB-stored parameters).
# ---------------------------------------------------------------------------

LOCKOUT_MAX_FAILURES: int = 10
LOCKOUT_WINDOW_MINUTES: int = 15


def _hash_email_for_lockout(email: str) -> str:
    """SHA-256 hex digest of the lowercased email address.

    Used as `email_hash` on `user_login_failures` rows for unknown-email
    stuffing attempts.  Storing the hash (not the plaintext) means the
    lockout table doesn't accumulate PII for non-existent addresses.
    """
    return hashlib.sha256(email.lower().encode("utf-8")).hexdigest()


def record_login_failure(db: Session, *, email: str, client_ip: str | None) -> None:
    """Insert a UserLoginFailure row.

    Accepts any email; `user_id` is filled when a matching User exists.
    Always records the `email_hash` so phantom-account stuffing is also
    rate-limited.

    Called BEFORE the response is returned so the row is committed even
    when the route raises HTTPException.  The caller must commit.
    """
    from app.domain.users.models import User, UserLoginFailure

    user = db.query(User).filter(User.email == email).first()
    db.add(
        UserLoginFailure(
            user_id=user.id if user else None,
            email_hash=_hash_email_for_lockout(email),
            client_ip=client_ip,
        )
    )


def check_login_lockout(db: Session, *, email: str) -> bool:
    """Return True if the account for ``email`` is locked out.

    Counts failure rows within the last LOCKOUT_WINDOW_MINUTES for
    either the user's id (if they exist) or the email hash (for
    unknown-email stuffing).

    Constant-time on the DB side: we always do the hash and always
    query — no short-circuit on "user not found" — so the response
    time doesn't reveal whether the email exists.
    """
    from sqlalchemy import or_

    from app.domain.users.models import User, UserLoginFailure

    cutoff = utcnow() - timedelta(minutes=LOCKOUT_WINDOW_MINUTES)
    email_hash = _hash_email_for_lockout(email)
    user = db.query(User).filter(User.email == email).first()
    user_id = user.id if user else None

    q = db.query(UserLoginFailure).filter(
        UserLoginFailure.occurred_at >= cutoff
    )
    if user_id is not None:
        q = q.filter(
            or_(
                UserLoginFailure.user_id == user_id,
                UserLoginFailure.email_hash == email_hash,
            )
        )
    else:
        q = q.filter(UserLoginFailure.email_hash == email_hash)

    count = q.count()
    return count >= LOCKOUT_MAX_FAILURES


def clear_login_failures(db: Session, *, email: str) -> None:
    """Delete all login failure rows for ``email`` after a successful login."""
    from app.domain.users.models import User, UserLoginFailure

    user = db.query(User).filter(User.email == email).first()
    if not user:
        return
    db.query(UserLoginFailure).filter(
        UserLoginFailure.user_id == user.id
    ).delete(synchronize_session=False)


def purge_expired_sessions(db: Session, *, now: datetime | None = None) -> int:
    """Delete every session row no longer accepted by auth.

    Idempotent: safe to call repeatedly. Returns the number of rows
    purged. Backed by `ix_user_sessions_expires_at` (alembic 0019) and
    `ix_user_sessions_last_used_at` (alembic 0057), so the DELETE can use
    indexed range scans.

    DB-007 / issue #98. Called on a one-hour cadence by the
    `session-purge` CLI job in the backend cron sidecar. The plan was to
    keep these rows around forever (sessions were only deleted on explicit
    logout); a long-running prod accumulates every expired row otherwise.
    """
    from sqlalchemy import or_

    from app.domain.users.models import UserSession

    cutoff = now or utcnow()
    idle_cutoff = cutoff - timedelta(hours=settings().SESSION_IDLE_HOURS)
    deleted = (
        db.query(UserSession)
        .filter(
            or_(
                UserSession.expires_at < cutoff,
                UserSession.last_used_at < idle_cutoff,
            )
        )
        .delete(synchronize_session=False)
    )
    return int(deleted)
