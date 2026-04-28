from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.domain._mixins import WorkspaceOwned
from app.infra.db import Base


class Tag(WorkspaceOwned, Base):
    __tablename__ = "tags"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_tag_ws_name"),
    )

    name = Column(String(120), nullable=False)
    color = Column(String(20), nullable=True)


class TagLink(WorkspaceOwned, Base):
    __tablename__ = "tag_links"
    __table_args__ = (
        UniqueConstraint("workspace_id", "tag_id", "object_type", "object_id", name="uq_tag_link"),
        Index("ix_tag_link_object", "workspace_id", "object_type", "object_id"),
    )

    tag_id = Column(UUID(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), nullable=False)
    object_type = Column(String(40), nullable=False)
    object_id = Column(UUID(as_uuid=True), nullable=False)
