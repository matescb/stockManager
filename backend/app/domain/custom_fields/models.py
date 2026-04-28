from __future__ import annotations

from sqlalchemy import Column, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.domain._mixins import WorkspaceOwned
from app.infra.db import Base


class CustomField(WorkspaceOwned, Base):
    __tablename__ = "custom_fields"
    __table_args__ = (
        UniqueConstraint("workspace_id", "object_type", "object_id", "key", name="uq_cf_unique"),
        Index("ix_cf_object", "workspace_id", "object_type", "object_id"),
    )

    object_type = Column(String(40), nullable=False)
    object_id = Column(UUID(as_uuid=True), nullable=False)
    key = Column(String(256), nullable=False)
    value = Column(String(1024), nullable=True)
