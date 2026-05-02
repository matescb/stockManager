from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID

from app.infra.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(320), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    password_hash = Column(String(500), nullable=False)
    locale = Column(String(20), nullable=False, default="en")
    timezone = Column(String(64), nullable=False, default="UTC")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    last_login_at = Column(DateTime(timezone=True), nullable=True)


class UserSession(Base):
    __tablename__ = "user_sessions"

    # The session row is keyed by the SHA-256 hex digest of the
    # plaintext cookie token (`token_hash`). The plaintext lives only
    # on the client cookie and is never persisted, so a DB dump cannot
    # be replayed as a session credential. Mirrors the invitation token
    # hashing landed in 0014 (SEC2-003).
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    # Sliding expiry (SEC2-015): bumped on every successful auth lookup.
    # A session idle past SESSION_IDLE_HOURS is rejected even if
    # `expires_at` (the absolute lifetime) is still in the future.
    last_used_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=False)
