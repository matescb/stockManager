from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from app.domain._mixins import WorkspaceOwned
from app.infra.db import Base

# Where a library entry came from. Server-controlled — a client never
# sets it, so a row's provenance survives whatever the uploader claims.
# `manual` is a hand-uploaded file; the rest name the vendor whose zip
# the phase-3 importer unpacked.
EDA_SOURCES = ("manual", "snapeda", "samacsys", "ultralibrarian", "easyeda")

# `step` and `wrl` are 3D models a footprint can reference; `spice` is a
# simulation model a part points at through `part_eda.spice_datafile_id`.
EDA_DATAFILE_KINDS = ("step", "wrl", "spice")


class EdaSymbol(WorkspaceOwned, Base):
    """A schematic symbol hosted by this workspace.

    `name` is the KiCad entry name — the `Entry` half of the
    `LibNick:Entry` reference the phase-5 HTTP-library endpoint will
    serve. The file itself is content-addressed on disk (see
    `storage.py`); `sha256` locates it and `size_bytes` is carried for
    the UI so listing a library doesn't have to stat every file.
    """

    __tablename__ = "eda_symbols"
    __table_args__ = (
        # Active-rows-only uniqueness, the same shape categories and tags
        # use: archiving an entry frees its name for re-use, and
        # `service.restore_*` re-checks on the way back.
        Index(
            "uq_eda_symbols_ws_name",
            "workspace_id",
            "name",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
        Index("ix_eda_symbols_ws_archived", "workspace_id", "archived_at"),
    )

    name = Column(String(200), nullable=False)
    sha256 = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    source = Column(String(20), nullable=False, default="manual", server_default=text("'manual'"))
    # Lets a workspace group its library the way it groups parts. SET NULL
    # rather than CASCADE: hard-deleting a category must never take the
    # symbol file with it.
    category_id = Column(
        UUID(as_uuid=True),
        ForeignKey("part_categories.id", ondelete="SET NULL"),
        nullable=True,
    )


class EdaFootprint(WorkspaceOwned, Base):
    """A PCB footprint hosted by this workspace.

    Same columns and same rules as `EdaSymbol` — `name` is the entry
    name inside a `.pretty` library. 3D models attach through
    `EdaFootprintModel` rather than living on this row, because one
    footprint routinely carries both a STEP and a WRL.
    """

    __tablename__ = "eda_footprints"
    __table_args__ = (
        Index(
            "uq_eda_footprints_ws_name",
            "workspace_id",
            "name",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
        Index("ix_eda_footprints_ws_archived", "workspace_id", "archived_at"),
    )

    name = Column(String(200), nullable=False)
    sha256 = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    source = Column(String(20), nullable=False, default="manual", server_default=text("'manual'"))
    category_id = Column(
        UUID(as_uuid=True),
        ForeignKey("part_categories.id", ondelete="SET NULL"),
        nullable=True,
    )


class EdaDatafile(WorkspaceOwned, Base):
    """A 3D model (STEP / WRL) or a SPICE model file.

    One table for all three because they share every column and every
    lifecycle rule; `kind` is what tells them apart, and it participates
    in the unique index so a `foo.step` and a `foo.spice` can coexist.
    """

    __tablename__ = "eda_datafiles"
    __table_args__ = (
        Index(
            "uq_eda_datafiles_ws_kind_name",
            "workspace_id",
            "kind",
            "name",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
        Index("ix_eda_datafiles_ws_archived", "workspace_id", "archived_at"),
    )

    kind = Column(String(10), nullable=False)
    name = Column(String(200), nullable=False)
    sha256 = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    source = Column(String(20), nullable=False, default="manual", server_default=text("'manual'"))


class EdaFootprintModel(Base):
    """Join row: a 3D model attached to a footprint at `position`.

    Plain `Base` with an explicit `workspace_id` rather than
    `WorkspaceOwned` — a join row has no independent lifecycle, so
    `archived_at` / `created_by` would be dead columns. Same shape as
    `parts.models.PartCadKey`. The column is still carried (and still
    filtered on in every query) because a join row that only reached its
    workspace through its parents couldn't be listed without a join.
    """

    __tablename__ = "eda_footprint_models"
    __table_args__ = (
        UniqueConstraint("footprint_id", "datafile_id", name="uq_eda_footprint_model"),
        Index("ix_eda_footprint_models_ws_footprint", "workspace_id", "footprint_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    footprint_id = Column(
        UUID(as_uuid=True),
        ForeignKey("eda_footprints.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    datafile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("eda_datafiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position = Column(Integer, nullable=False, default=0, server_default=text("0"))


class PartEda(WorkspaceOwned, Base):
    """The EDA configuration for one part — at most one row per part.

    Two ways to name a symbol or a footprint, and only one may be set:

    * `symbol_id` / `footprint_id` point at a definition this workspace
      hosts (uploaded here, served to KiCad from here).
    * `symbol_ref_external` / `footprint_ref_external` hold a KiCad
      `LibNick:Entry` string — "Device:R" — naming something in the
      libraries the user already has installed locally. We store the
      reference and never the file.

    Both null means "inherit the category default", resolved when the
    KiCad HTTP-library endpoint lands in phase 5. The XOR is enforced
    three ways: a 422 in the service, the CHECK constraints below, and
    the same CHECKs in migration 0068.

    `archived_at` comes from the mixin but is never set — the config is
    deleted outright (`DELETE /api/parts/{id}/eda`), because a part with
    a soft-archived config would still hit `uq_part_eda_part` on the way
    back and there is nothing here worth keeping a tombstone for.
    """

    __tablename__ = "part_eda"
    __table_args__ = (
        CheckConstraint(
            "NOT (symbol_id IS NOT NULL AND symbol_ref_external IS NOT NULL)",
            name="ck_part_eda_symbol_ref_exclusive",
        ),
        CheckConstraint(
            "NOT (footprint_id IS NOT NULL AND footprint_ref_external IS NOT NULL)",
            name="ck_part_eda_footprint_ref_exclusive",
        ),
        UniqueConstraint("part_id", name="uq_part_eda_part"),
        Index("ix_part_eda_ws_part", "workspace_id", "part_id"),
    )

    part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("parts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    symbol_id = Column(
        UUID(as_uuid=True),
        ForeignKey("eda_symbols.id", ondelete="SET NULL"),
        nullable=True,
    )
    symbol_ref_external = Column(String(200), nullable=True)
    footprint_id = Column(
        UUID(as_uuid=True),
        ForeignKey("eda_footprints.id", ondelete="SET NULL"),
        nullable=True,
    )
    footprint_ref_external = Column(String(200), nullable=True)
    spice_datafile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("eda_datafiles.id", ondelete="SET NULL"),
        nullable=True,
    )

    # KiCad symbol fields. `value` overrides what the schematic shows for
    # this part; `keywords` and `footprint_filters` drive the symbol and
    # footprint choosers.
    value = Column(String(120), nullable=True)
    keywords = Column(String(300), nullable=True)
    footprint_filters = Column(ARRAY(String(100)), nullable=True)

    exclude_from_bom = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    exclude_from_board = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Defaults true: most parts have no simulation model, and KiCad treats
    # a part that claims to be simulatable but isn't as an error.
    exclude_from_sim = Column(Boolean, nullable=False, default=True, server_default=text("true"))

    # SPICE `Sim.*` symbol fields. Free-form strings — KiCad parses them,
    # we only carry them.
    sim_device = Column(String(60), nullable=True)
    sim_pins = Column(String(300), nullable=True)
    sim_params = Column(String(500), nullable=True)
