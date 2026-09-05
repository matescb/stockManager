from __future__ import annotations

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
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
        # DB-005 / migration 0032 — quantities are integer-only (electronics
        # domain; no fractional BOM quantities needed).
        CheckConstraint("quantity >= 0", name="ck_project_entries_quantity_nonneg"),
        # Track B1 / migration 0072 — per-BOM-line attrition (waste rate).
        # 0 <= pct < 100; 100% waste would demand infinite stock per placed
        # part. Compounds with the part-intrinsic attrition in
        # builds/service.py::_required.
        CheckConstraint(
            "attrition_pct >= 0 AND attrition_pct < 100",
            name="ck_project_entries_attrition_pct_range",
        ),
    )

    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    entry_type = Column(String(20), nullable=False, default="part")  # part|meta_part|non_part|unmatched
    part_id = Column(UUID(as_uuid=True), ForeignKey("parts.id", ondelete="SET NULL"), nullable=True)
    meta_part_id = Column(UUID(as_uuid=True), ForeignKey("parts.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(300), nullable=True)
    quantity = Column(Integer, nullable=False, default=1)
    # Per-BOM-line waste rate (Track B1, migration 0072). Inflates the
    # required + consumed quantity for a build; see
    # builds/service.py::_required. server_default keeps the NOT NULL add
    # safe on populated tables.
    attrition_pct = Column(Numeric(6, 4), nullable=False, server_default="0", default=0)
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
