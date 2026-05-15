from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.core.time import utcnow
from app.infra.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(320), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    password_hash = Column(String(500), nullable=False)
    locale = Column(String(20), nullable=False, default="en")
    timezone = Column(String(64), nullable=False, default="UTC")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_login_at = Column(DateTime(timezone=True), nullable=True)


class UserSession(Base):
    __tablename__ = "user_sessions"

    # The session row is keyed by the SHA-256 hex digest of the
    # plaintext cookie token (`token_hash`). The plaintext lives only
    # on the client cookie and is never persisted, so a DB dump cannot
    # be replayed as a session credential. Mirrors the invitation token
    # hashing landed in 0014 (SEC2-003).
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    # Sliding expiry (SEC2-015): bumped on every successful auth lookup.
    # A session idle past SESSION_IDLE_HOURS is rejected even if
    # `expires_at` (the absolute lifetime) is still in the future.
    last_used_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    # Purge indexes (`ix_user_sessions_expires_at` from alembic 0019 and
    # `ix_user_sessions_last_used_at` from alembic 0057) live in migrations
    # rather than `index=True` here. Don't add `index=True` — SQLAlchemy
    # would emit redundant CREATE INDEX statements in a future autogenerate
    # run.
    expires_at = Column(DateTime(timezone=True), nullable=False)


class UserLoginFailure(Base):
    """Per-account login failure record for the account-lockout mechanism.

    Each failed login attempt appends one row.  The lockout check counts
    rows within the last LOCKOUT_WINDOW_MINUTES minutes; a successful login
    deletes all rows for the user.

    `user_id` is SET NULL on user deletion so we retain the IP-based audit
    trail without a dangling FK.  Rows with `user_id IS NULL` are orphaned
    tombstones — they are still counted for the phantom-account cap (see
    `auth.py::_check_login_lockout`) while not leaking user existence.

    NOTE: the account-lockout rows are intentionally NOT workspace-scoped.
    Login is a pre-workspace operation and the lockout table protects the
    user credential, not any particular workspace resource.
    """

    __tablename__ = "user_login_failures"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # SET NULL on delete preserves the audit trail for orphaned sessions.
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # The email is stored as a SHA-256 hex digest (normalised to lowercase)
    # so we can count failures for unknown-email attempts without storing
    # PII and without revealing whether the address exists in the DB.
    email_hash = Column(String(64), nullable=False, index=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    client_ip = Column(String(45), nullable=True)  # IPv4 or IPv6


class PasswordResetRequest(Base):
    """Transient password reset request.

    Password reset is a pre-authentication flow, so this table is not
    workspace-scoped. The email is stored as a hash for throttle rows;
    reset tokens are HMACed at rest and are single-use via `used_at`.
    """

    __tablename__ = "password_reset_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    email_hash = Column(String(64), nullable=False, index=True)
    token_hmac = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    used_at = Column(DateTime(timezone=True), nullable=True)
    ip = Column(String(45), nullable=True, index=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)


class PendingUser(Base):
    """Transient signup record held until the user verifies their email.

    Created by POST /auth/signup; consumed (and promoted to User +
    Workspace + WorkspaceMember) by POST /auth/verify in a single
    transaction.  Rows older than 24 h are treated as expired.

    NOTE: this table is intentionally NOT workspace-scoped.  Signup
    precedes workspace creation — there is no workspace to scope against.
    This is one of the few tables in the app that lacks a workspace_id FK.
    Do NOT add workspace_id here; see the workspace-isolation hard
    invariant in CLAUDE.md.
    """

    __tablename__ = "pending_users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(320), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    password_hash = Column(String(500), nullable=False)
    workspace_name = Column(String(200), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    # HMAC-SHA-256 (keyed on SESSION_SECRET) of the plaintext verification
    # token.  The plaintext is sent to the user's email and never stored.
    # Verification compares hmac.compare_digest(hmac_of_supplied, this col)
    # for constant-time comparison.
    verification_token_hmac = Column(String(64), nullable=False)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    ip = Column(String(45), nullable=True)
    # Optional free-form notes — not used by the core flow.
    notes = Column(Text, nullable=True)
