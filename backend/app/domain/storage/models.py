from __future__ import annotations

from sqlalchemy import Boolean, Column, Index, String, Text, text

from app.domain._mixins import WorkspaceOwned
from app.infra.db import Base


class StorageLocation(WorkspaceOwned, Base):
    __tablename__ = "storage_locations"
    __table_args__ = (
        # Partial unique on active rows only (alembic 0018, DB-003).
        # Archived rows free up the name for re-use, mirroring how the
        # parts MPN partial unique works.
        Index(
            "uq_storage_ws_name",
            "workspace_id",
            "name",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
        Index("ix_storage_ws_archived", "workspace_id", "archived_at"),
        # pg_trgm GIN index for ILIKE %q% search (alembic 0018, BE2-018).
        Index(
            "ix_storage_ws_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    single_part_only = Column(Boolean, nullable=False, default=False)
    existing_parts_only = Column(Boolean, nullable=False, default=False)
    is_full = Column(Boolean, nullable=False, default=False)
