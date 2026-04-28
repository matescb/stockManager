from __future__ import annotations

from sqlalchemy import BigInteger, Column, Index, String
from sqlalchemy.dialects.postgresql import UUID

from app.domain._mixins import WorkspaceOwned
from app.infra.db import Base


class Attachment(WorkspaceOwned, Base):
    __tablename__ = "attachments"
    __table_args__ = (
        Index("ix_attach_object", "workspace_id", "object_type", "object_id"),
    )

    object_type = Column(String(40), nullable=False)
    object_id = Column(UUID(as_uuid=True), nullable=False)
    file_name = Column(String(400), nullable=False)
    file_type = Column(String(40), nullable=False, default="other")
    mime_type = Column(String(120), nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    storage_key = Column(String(800), nullable=False)
    uploaded_by = Column(UUID(as_uuid=True), nullable=True)
