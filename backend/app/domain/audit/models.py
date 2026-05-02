"""SQLAlchemy model for the audit_log table (BE2-024)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from app.infra.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action = Column(Text, nullable=False)
    target_type = Column(Text, nullable=True)
    # PostgreSQL UUID[] — GIN-indexed in migration 0024 (audit_log).
    target_ids = Column(ARRAY(UUID(as_uuid=True)), nullable=True)
    comment = Column(Text, nullable=True)
    request_id = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
