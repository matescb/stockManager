from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from app.core.time import utcnow
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
    # Where the model sits on the footprint when the footprint's own bytes
    # don't say: `{"offset": […], "rotate": […], "scale": […]}` in KiCad's
    # 3D frame. NULL = unknown (appended at the identity). Migration 0084;
    # read through `model_fit.Placement.from_json`.
    # `none_as_null`: Python None must be SQL NULL, not JSON `null`, or
    # `placement IS NULL` (the backfill job's predicate) never matches.
    placement = Column(JSONB(none_as_null=True), nullable=True)


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


class PartSpiceBinding(WorkspaceOwned, Base):
    """How a part's symbol pins map onto its SPICE model's ports (SM-009).

    At most one row per part, beside `PartEda` rather than inside it:
    `set_part_eda` REPLACES the whole `part_eda` row from its arguments,
    and a binding stored there would be wiped by every call that did not
    know to pass it back. `part_eda.sim_pins` stays the carrier the KiCad
    HTTP library emits as `Sim.Pins`; this row is the structured source a
    validated value is DERIVED from (`spice_binding.py::bind`).

    `model_sha256` + `spice_datafile_id` pin the exact file the ports were
    read from. Whether the binding still holds is decided at READ time
    (`spice_binding.binding_state`): a different file or different bytes
    on the part, or a hand-edited `sim_pins`, make it unvalidated without
    anything having to remember to flip a flag. `validated` is the
    write-time verdict; it is set false (never deleted) when a new file
    version is uploaded onto the part.

    Migration 0087. No workspace BEFORE trigger, for the reason 0068
    gives for the other EDA tables: both FKs are resolved through
    workspace-scoped lookups in the only code that writes this row.
    """

    __tablename__ = "part_spice_bindings"
    __table_args__ = (
        UniqueConstraint("part_id", name="uq_part_spice_bindings_part"),
        CheckConstraint(
            "model_kind IN ('subckt', 'model', 'native_primitive', 'none')",
            name="ck_part_spice_bindings_model_kind",
        ),
        CheckConstraint(
            "model_kind NOT IN ('subckt', 'model') "
            "OR (subckt IS NOT NULL AND model_sha256 IS NOT NULL)",
            name="ck_part_spice_bindings_file_model",
        ),
        Index("ix_part_spice_bindings_ws_part", "workspace_id", "part_id"),
    )

    part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("parts.id", ondelete="CASCADE"),
        nullable=False,
    )
    spice_datafile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("eda_datafiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    # `.subckt` name, or the `.model` name for a primitive device. NULL
    # for the two file-less kinds: `native_primitive` (a built-in R / C /
    # L / D with `params`, no file) and `none` (no model; see `fallback`).
    subckt = Column(String(200), nullable=True)
    model_kind = Column(
        String(20), nullable=False, default="subckt", server_default=text("'subckt'")
    )
    device_type = Column(String(20), nullable=True)
    ports = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    # {symbol_pin_number: port_name | null}; null = explicitly not connected.
    # NULL column = parsed from an upload, no mapping confirmed yet.
    pin_map = Column(JSONB(none_as_null=True), nullable=True)
    symbol_pins_source = Column(String(20), nullable=True)
    sim_pins_derived = Column(String(300), nullable=True)
    model_sha256 = Column(String(64), nullable=True)
    # Model ports deliberately driven by no symbol pin (LTspice NC nodes,
    # the other channels of a multi-channel part).
    unconnected_ports = Column(JSONB(none_as_null=True), nullable=True)
    params = Column(String(500), nullable=True)
    fallback = Column(String(500), nullable=True)
    # ngspice-export/1 fallback token: vendor_model_wanted | native |
    # attended_ltspice | external_model:<name>.
    fallback_token = Column(String(220), nullable=True)
    # The inventory manifest's `native` object for a native_primitive:
    # {letter, parameters {value, unit, source} | null, parameters_reason,
    # loses_behaviour, loses_behaviour_reason}.
    native = Column(JSONB(none_as_null=True), nullable=True)
    # The ngspice-export/1 header fields of the bound file: kind, build
    # token, valid_for_basis, per-port function / on_symbol /
    # internally_unconnected, channels, match, corrections.
    export_metadata = Column(JSONB(none_as_null=True), nullable=True)
    model_source = Column(String(500), nullable=True)
    build_requirement = Column(String(500), nullable=True)
    grade = Column(String(500), nullable=True)
    header_pins = Column(JSONB(none_as_null=True), nullable=True)
    validated = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    validated_at = Column(DateTime(timezone=True), nullable=True)


# SM-010 vocabularies. Kept here beside the model so the CHECK
# constraints, the migration and the service read one list.
ASSET_KINDS = ("symbol", "footprint", "model3d", "spice")
MODEL_KINDS = ("vendor", "converted", "fitted")
REVIEW_STATES = ("unreviewed", "reviewed", "rejected")


class EdaAssetMetadata(WorkspaceOwned, Base):
    """What is known about ONE version of one hosted EDA file (SM-010).

    Keyed by the asset row AND its `sha256`: replacing a file's bytes
    moves the row's `sha256` (`service.update_entry` re-stores in place),
    so the lookup for the new version misses and the new version reads
    as `unreviewed` with no metadata — nothing has to remember to reset
    it. The old version's row stays as history.

    Exactly one of `symbol_id` / `footprint_id` / `datafile_id` is set
    (3D models and SPICE files both live in `eda_datafiles`). Real FKs
    with CASCADE rather than a polymorphic `object_id`, so a hard-deleted
    library entry takes its metadata with it without a cleanup listener.

    `source` on the asset row stays what it is — the server-set import
    channel. `origin` here is where the file came from in the human
    sense (a URL, "datasheet rev C"), written by whoever knows.

    The review columns are written only by `PUT /api/parts/{id}/eda/
    {kind}/review` from a browser session of an admin/owner; no API
    token can change them. Migration 0088.
    """

    __tablename__ = "eda_asset_metadata"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(symbol_id, footprint_id, datafile_id) = 1",
            name="ck_eda_asset_metadata_one_asset",
        ),
        CheckConstraint(
            "model_kind IS NULL OR model_kind IN ('vendor', 'converted', 'fitted')",
            name="ck_eda_asset_metadata_model_kind",
        ),
        CheckConstraint(
            "review_state IN ('unreviewed', 'reviewed', 'rejected')",
            name="ck_eda_asset_metadata_review_state",
        ),
        Index(
            "uq_eda_asset_metadata_symbol_sha",
            "symbol_id",
            "sha256",
            unique=True,
            postgresql_where=text("symbol_id IS NOT NULL"),
        ),
        Index(
            "uq_eda_asset_metadata_footprint_sha",
            "footprint_id",
            "sha256",
            unique=True,
            postgresql_where=text("footprint_id IS NOT NULL"),
        ),
        Index(
            "uq_eda_asset_metadata_datafile_sha",
            "datafile_id",
            "sha256",
            unique=True,
            postgresql_where=text("datafile_id IS NOT NULL"),
        ),
    )

    symbol_id = Column(
        UUID(as_uuid=True), ForeignKey("eda_symbols.id", ondelete="CASCADE"), nullable=True
    )
    footprint_id = Column(
        UUID(as_uuid=True), ForeignKey("eda_footprints.id", ondelete="CASCADE"), nullable=True
    )
    datafile_id = Column(
        UUID(as_uuid=True), ForeignKey("eda_datafiles.id", ondelete="CASCADE"), nullable=True
    )
    sha256 = Column(String(64), nullable=False)

    origin = Column(String(1000), nullable=True)
    model_kind = Column(String(12), nullable=True)
    simulators = Column(ARRAY(String(40)), nullable=True)
    valid_for = Column(ARRAY(String(120)), nullable=True)
    not_valid_for = Column(ARRAY(String(120)), nullable=True)
    note = Column(String(2000), nullable=True)

    review_state = Column(
        String(12), nullable=False, default="unreviewed", server_default=text("'unreviewed'")
    )
    reviewed_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_against = Column(String(500), nullable=True)


PIN_PAD_FINDING_KINDS = ("pad_without_pin", "pin_without_pad", "duplicate_pin")
PIN_PAD_FINDING_CLASSES = ("exposed_pad", "nc_pin", "shell", "mechanical", "electrical")


class PinPadAcceptance(Base):
    """A human's "this pin/pad finding is fine" for one part (SM-008b).

    Tied to BOTH file hashes the finding was seen on: replace the symbol
    or the footprint and the acceptance no longer applies — the check
    lists it under `stale_acceptances` instead of quietly excusing a
    finding on files nobody looked at. Accepting the same finding on the
    same files again updates the row (note, who, when); accepting it on
    new files is a new row.

    `finding_class` is the class the finding had when it was accepted,
    kept for the record (the column is not `class`, a reserved word).
    Written only by `POST /api/parts/{id}/eda/pin-pad/accept` from an
    owner/admin browser session; MCP reads it and never writes it.
    """

    __tablename__ = "pin_pad_acceptances"
    __table_args__ = (
        CheckConstraint(
            "finding_kind IN ('pad_without_pin', 'pin_without_pad', 'duplicate_pin')",
            name="ck_pin_pad_acceptances_kind",
        ),
        CheckConstraint(
            "finding_class IN ('exposed_pad', 'nc_pin', 'shell', 'mechanical', 'electrical')",
            name="ck_pin_pad_acceptances_class",
        ),
        Index(
            "uq_pin_pad_acceptances_finding",
            "workspace_id",
            "part_id",
            "symbol_sha256",
            "footprint_sha256",
            "finding_kind",
            "number",
            unique=True,
        ),
        Index("ix_pin_pad_acceptances_ws_part", "workspace_id", "part_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("parts.id", ondelete="CASCADE"),
        nullable=False,
    )
    symbol_sha256 = Column(String(64), nullable=False)
    footprint_sha256 = Column(String(64), nullable=False)
    finding_kind = Column(String(20), nullable=False)
    number = Column(String(100), nullable=False)
    finding_class = Column(String(20), nullable=False)
    accepted_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    accepted_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    note = Column(Text, nullable=True)
