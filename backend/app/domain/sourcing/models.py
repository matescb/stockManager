"""SQLAlchemy models for TrustedParts sourcing."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.time import utcnow
from app.infra.db import Base


class SourcingCache(Base):
    """Workspace-scoped short-lived cache for TrustedParts API responses."""

    __tablename__ = "sourcing_cache"
    __table_args__ = (
        CheckConstraint(
            "expires_at <= fetched_at + interval '7 days'",
            name="sourcing_cache_max_7_day_ttl",
        ),
        Index("uq_sourcing_cache_ws_qhash", "workspace_id", "query_hash", unique=True),
        Index("ix_sourcing_cache_expires_at", "expires_at"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa.text("gen_random_uuid()"),
    )
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    query_hash = Column(sa.CHAR(length=64), nullable=False)
    query_json = Column(JSONB, nullable=False)
    response_json = Column(JSONB, nullable=False)
    fetched_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=sa.func.now(),
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_by = Column(UUID(as_uuid=True), nullable=True)
