from __future__ import annotations

from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.domain._mixins import WorkspaceOwned
from app.infra.db import Base


class Project(WorkspaceOwned, Base):
    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_ws_name", "workspace_id", "name"),
        Index("ix_projects_ws_archived", "workspace_id", "archived_at"),
        # pg_trgm GIN index for ILIKE %q% search (alembic 0018, BE2-018).
        Index(
            "ix_projects_ws_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    name = Column(String(300), nullable=False)
    description = Column(Text, nullable=True)
    notes_markdown = Column(Text, nullable=True)
    associated_subassembly_part_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "parts.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_projects_associated_subassembly_part",
        ),
        nullable=True,
    )


class ProjectEntry(WorkspaceOwned, Base):
    __tablename__ = "project_entries"
    __table_args__ = (
        Index("ix_project_entries_proj", "workspace_id", "project_id"),
        Index("ix_project_entries_part", "workspace_id", "part_id"),
        # (workspace_id, archived_at) partial composite added in
        # alembic 0018 (DB-004) for the universal active-row filter.
        Index(
            "ix_project_entries_ws_archived",
            "workspace_id",
            "archived_at",
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    entry_type = Column(String(20), nullable=False, default="part")  # part|meta_part|non_part|unmatched
    part_id = Column(UUID(as_uuid=True), ForeignKey("parts.id", ondelete="SET NULL"), nullable=True)
    meta_part_id = Column(UUID(as_uuid=True), ForeignKey("parts.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(300), nullable=True)
    quantity = Column(Numeric(18, 6), nullable=False, default=1)
    comments = Column(Text, nullable=True)
    designators = Column(ARRAY(String), nullable=False, default=list)
    cad_footprint = Column(String(120), nullable=True)
    cad_key = Column(String(300), nullable=True)
    dnp = Column(Boolean, nullable=False, default=False)
    order_index = Column(Integer, nullable=False, default=0)


class BomImportPreset(WorkspaceOwned, Base):
    __tablename__ = "bom_import_presets"
    __table_args__ = (
        # (workspace_id, archived_at) partial composite added in
        # alembic 0018 (DB-004) for the universal active-row filter.
        Index(
            "ix_bom_import_presets_ws_archived",
            "workspace_id",
            "archived_at",
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    name = Column(String(200), nullable=False)
    config_json = Column(Text, nullable=False)
