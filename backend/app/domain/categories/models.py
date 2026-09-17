from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from app.domain._mixins import WorkspaceOwned
from app.infra.db import Base


class PartCategory(WorkspaceOwned, Base):
    """A workspace-scoped bucket for parts (resistors, MCUs, connectors…).

    The KiCad-facing columns (`refdes_prefix`, `default_symbol_ref`,
    `default_footprint_ref`, `footprint_filters`, `library_slug`,
    `value_template`, `kicad_fields`) carry the metadata served over the
    KiCad HTTP-library protocol; they are inert for every other consumer.
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

    # What a part in this category shows as its schematic `Value`, built
    # from the part's canonical specs — "{resistance} {tolerance}
    # {package}" renders "10 kΩ 1% 0603". Rendering rules and the
    # placeholder grammar live in `domain/eda/value_template.py`; the
    # shape is validated in `schemas.py`, not by a CHECK constraint (the
    # vocabulary of legal keys is application data). Alembic 0082.
    #
    # NULL means "inherit": `kicad_library.py` walks `parent_id` for the
    # nearest ancestor that has one, so a template set on *Capacitors*
    # covers *Capacitors / Ceramic* without being repeated.
    value_template = Column(String(200), nullable=True)
    # Canonical spec keys to emit as hidden KiCad symbol fields, in
    # order — ["resistance", "tolerance"] becomes `Resistance` and
    # `Tolerance` on every symbol in the category. JSONB rather than a
    # text array so a later phase can carry per-key options without a
    # migration. Inherits through `parent_id` the same way, and
    # independently of `value_template`.
    kicad_fields = Column(JSONB, nullable=True)
    # Which canonical spec keys the parts list shows as columns when it is
    # filtered to this category, in order — ["resistance", "tolerance"]
    # becomes a Resistance and a Tolerance column. Alembic 0083.
    #
    # JSONB rather than a text array for the reason 0082 gives for
    # `kicad_fields`: a later phase will want per-key display options
    # (width, unit override) and JSONB takes them without a migration.
    # Validated against the category's effective spec schema by
    # `domain/parts/services/spec_columns.py`, not by a CHECK constraint —
    # the vocabulary of legal keys is application data.
    #
    # NULL means "inherit": the spec-schema endpoint walks `parent_id` for
    # the nearest ancestor that has one, so a choice made on *Resistors*
    # covers *Resistors / Thin film*. An EMPTY list is an explicit "no spec
    # columns" and stops the walk — the same NULL-vs-`[]` split
    # `kicad_fields` uses.
    list_columns = Column(JSONB, nullable=True)
    # The default sort that same listing applies when the request names
    # none — `{"key": "resistance", "dir": "asc"}`. Inherits through
    # `parent_id` the same way, and independently of `list_columns`.
    list_sort = Column(JSONB, nullable=True)

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
