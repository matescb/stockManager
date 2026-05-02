from __future__ import annotations

from sqlalchemy import BigInteger, Column, Index, String, text
from sqlalchemy.dialects.postgresql import UUID

from app.domain._mixins import WorkspaceOwned
from app.infra.db import Base


class Attachment(WorkspaceOwned, Base):
    __tablename__ = "attachments"
    __table_args__ = (
        Index("ix_attach_object", "workspace_id", "object_type", "object_id"),
        # (workspace_id, archived_at) partial composite added in
        # alembic 0018 (DB-004) for the universal active-row filter.
        Index(
            "ix_attachments_ws_archived",
            "workspace_id",
            "archived_at",
            postgresql_where=text("archived_at IS NULL"),
        ),
        # (workspace_id, object_id) — added in alembic 0031 (DB-006) to
        # make orphan-cleanup queries fast (no object_type filter needed
        # when sweeping a deleted parent's id).
        Index("ix_attachments_ws_objid_only", "workspace_id", "object_id"),
    )

    object_type = Column(String(40), nullable=False)
    object_id = Column(UUID(as_uuid=True), nullable=False)
    file_name = Column(String(400), nullable=False)
    file_type = Column(String(40), nullable=False, default="other")
    mime_type = Column(String(120), nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    storage_key = Column(String(800), nullable=False)
    uploaded_by = Column(UUID(as_uuid=True), nullable=True)
