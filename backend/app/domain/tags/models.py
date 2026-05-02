from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID

from app.domain._mixins import WorkspaceOwned
from app.infra.db import Base


class Tag(WorkspaceOwned, Base):
    __tablename__ = "tags"
    __table_args__ = (
        # Partial unique on active rows only (alembic 0018, DB-003).
        # Archived tags free up the name for re-use.
        Index(
            "uq_tag_ws_name",
            "workspace_id",
            "name",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    name = Column(String(120), nullable=False)
    color = Column(String(20), nullable=True)


class TagLink(WorkspaceOwned, Base):
    __tablename__ = "tag_links"
    __table_args__ = (
        UniqueConstraint("workspace_id", "tag_id", "object_type", "object_id", name="uq_tag_link"),
        Index("ix_tag_link_object", "workspace_id", "object_type", "object_id"),
        # (workspace_id, archived_at) partial composite added in
        # alembic 0018 (DB-004) for the universal active-row filter.
        Index(
            "ix_tag_links_ws_archived",
            "workspace_id",
            "archived_at",
            postgresql_where=text("archived_at IS NULL"),
        ),
        # (workspace_id, object_id) — added in alembic 0031 (DB-006) to
        # make orphan-cleanup queries fast (no object_type filter needed
        # when sweeping a deleted parent's id).
        Index("ix_tag_link_ws_objid_only", "workspace_id", "object_id"),
    )

    tag_id = Column(UUID(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), nullable=False)
    object_type = Column(String(40), nullable=False)
    object_id = Column(UUID(as_uuid=True), nullable=False)
