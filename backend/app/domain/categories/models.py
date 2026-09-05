from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID

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
        Index(
            "ix_part_categories_parent_id",
            "parent_id",
            postgresql_where=text("parent_id IS NOT NULL"),
        ),
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
    #
    # Deliberately still workspace-global, NOT sibling-scoped, now that the
    # table has a parent: `library_slug` is what `kicad_refs.py` turns into
    # the generated `SM_{slug}.kicad_sym` filename, so two same-named leaves
    # under different branches would silently collide onto one KiCad
    # library. Duplicate leaf names across branches are refused instead —
    # an accepted cost, revisit only as an explicit product decision.
    library_slug = Column(String(60), nullable=False)

    # Adjacency-list parent. NULL = a root of the tree. `ON DELETE SET NULL`
    # (alembic 0078) means deleting a mid-tree category promotes its
    # children to root rather than cascading the subtree away; cycles and
    # depth are capped in `domain/categories/tree.py`, not in SQL.
    parent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("part_categories.id", ondelete="SET NULL"),
        nullable=True,
    )
