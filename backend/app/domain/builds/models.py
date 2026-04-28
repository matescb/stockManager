from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.domain._mixins import WorkspaceOwned
from app.infra.db import Base


class Build(WorkspaceOwned, Base):
    __tablename__ = "builds"
    __table_args__ = (
        Index("ix_builds_ws_status", "workspace_id", "status"),
        Index("ix_builds_ws_project", "workspace_id", "project_id"),
        Index("ix_builds_ws_archived", "workspace_id", "archived_at"),
    )

    name = Column(String(200), nullable=False)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    status = Column(String(20), nullable=False, default="planned")  # planned|in_progress|complete|cancelled
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    output_lot_id = Column(UUID(as_uuid=True), ForeignKey("lots.id", ondelete="SET NULL"), nullable=True)
    comments = Column(Text, nullable=True)
