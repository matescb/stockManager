from __future__ import annotations

from sqlalchemy import Boolean, Column, Index, String, Text, UniqueConstraint

from app.domain._mixins import WorkspaceOwned
from app.infra.db import Base


class StorageLocation(WorkspaceOwned, Base):
    __tablename__ = "storage_locations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_storage_ws_name"),
        Index("ix_storage_ws_archived", "workspace_id", "archived_at"),
    )

    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    single_part_only = Column(Boolean, nullable=False, default=False)
    existing_parts_only = Column(Boolean, nullable=False, default=False)
    is_full = Column(Boolean, nullable=False, default=False)
