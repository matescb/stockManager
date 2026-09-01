from __future__ import annotations

from sqlalchemy import Column, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import ARRAY

from app.domain._mixins import WorkspaceOwned
from app.infra.db import Base


class PartCategory(WorkspaceOwned, Base):
    """A workspace-scoped bucket for parts (resistors, MCUs, connectors…).

    The KiCad-facing columns (`refdes_prefix`, `default_symbol_ref`,
    `default_footprint_ref`, `footprint_filters`, `library_slug`) carry the
    metadata a later phase serves over the KiCad HTTP-library protocol; they
    are inert for every other consumer.
    """

    __tablename__ = "part_categories"
    __table_args__ = (
        # Partial uniques on active rows only — mirrors `tags.uq_tag_ws_name`
        # (alembic 0018). Archiving a category frees its name and slug for
        # re-use; case-insensitive uniqueness is deliberately NOT enforced.
        Index(
            "uq_part_categories_ws_name",
            "workspace_id",
            "name",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
        Index(
            "uq_part_categories_ws_slug",
            "workspace_id",
            "library_slug",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
        Index("ix_part_categories_ws_archived", "workspace_id", "archived_at"),
    )

    name = Column(String(120), nullable=False)
    description = Column(String(500), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0, server_default=text("0"))
    # Schematic reference designator prefix — "R", "C", "U", …
    refdes_prefix = Column(String(10), nullable=True)
    # KiCad `LibNick:Entry` references, e.g. "Device:R" / "Resistor_SMD:R_0402_1005Metric".
    default_symbol_ref = Column(String(200), nullable=True)
    default_footprint_ref = Column(String(200), nullable=True)
    # KiCad footprint-chooser filter globs, e.g. ["R_*", "*_0402_*"].
    footprint_filters = Column(ARRAY(String(100)), nullable=True)
    # URL- and library-safe identifier, derived from `name` when the caller
    # doesn't supply one. Unique per workspace among active rows.
    library_slug = Column(String(60), nullable=False)
