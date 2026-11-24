"""Structured SPICE binding per part: `part_spice_bindings` (SM-009).

`part_eda.sim_pins` is free text that nothing validates: a wrong order
silently gives a wrong simulation. This table stores, for the SPICE model
a part points at, the subcircuit (or `.model`) name and the ordered port
list parsed from the file, a `{symbol_pin: port}` mapping checked against
the symbol, and the sha256 of the exact bytes it was checked against.
A validated binding DERIVES `part_eda.sim_pins`, which stays the field
the KiCad HTTP library emits as `Sim.Pins` — no emitted name changes.

A separate table rather than columns on `part_eda`, because
`set_part_eda` replaces that whole row from its arguments; columns there
would be reset by every caller that did not know to pass them back.

Two file-less kinds are first-class so a part without a model file is
not blank: `native_primitive` (a built-in R / C / L / D simulated with
`params`, no file) and `none` (no model; `fallback` names what to use
instead). For both, `subckt` and `model_sha256` are NULL; a CHECK keeps
them NOT NULL for the file kinds `subckt` / `model`.
`unconnected_ports` lists model ports deliberately driven by no symbol
pin. `fallback_token` and `native` carry the ngSPICE inventory manifest's
fallback token and native-primitive record; `export_metadata` holds the
`ngspice-export/1` header fields of the bound file (port functions,
internally unconnected ports, channels, kind and build tokens, match,
valid_for basis).

Metadata columns `model_source`, `build_requirement`, `grade` and
`header_pins` are filled from the NG-010 header `bin/export-model` of the
ngSPICE model library writes, when the file carries one.

**Additive only.** One new table, no backfill: existing `part_eda` rows
and their free-text `sim_pins` are not read or rewritten. A part with
free-text `sim_pins` and no binding reports `validated: false,
source: "free-text"` from the read path.

**Locks.** CREATE TABLE plus its indexes on an empty table; the FKs take
SHARE ROW EXCLUSIVE on `parts` / `eda_datafiles` / `workspaces` / `users`
for the instant of the DDL only.

Reversibility: total — `downgrade()` drops the table; the derived
`sim_pins` values it wrote stay on `part_eda` as ordinary free text.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "part_spice_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "part_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("parts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "spice_datafile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eda_datafiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("subckt", sa.String(length=200), nullable=True),
        sa.Column(
            "model_kind",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'subckt'"),
        ),
        sa.Column("device_type", sa.String(length=20), nullable=True),
        sa.Column(
            "ports",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("pin_map", postgresql.JSONB(), nullable=True),
        sa.Column("symbol_pins_source", sa.String(length=20), nullable=True),
        sa.Column("sim_pins_derived", sa.String(length=300), nullable=True),
        sa.Column("model_sha256", sa.String(length=64), nullable=True),
        sa.Column("unconnected_ports", postgresql.JSONB(), nullable=True),
        sa.Column("params", sa.String(length=500), nullable=True),
        sa.Column("fallback", sa.String(length=500), nullable=True),
        sa.Column("fallback_token", sa.String(length=220), nullable=True),
        sa.Column("native", postgresql.JSONB(), nullable=True),
        sa.Column("export_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("model_source", sa.String(length=500), nullable=True),
        sa.Column("build_requirement", sa.String(length=500), nullable=True),
        sa.Column("grade", sa.String(length=500), nullable=True),
        sa.Column("header_pins", postgresql.JSONB(), nullable=True),
        sa.Column(
            "validated", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("part_id", name="uq_part_spice_bindings_part"),
        sa.CheckConstraint(
            "model_kind IN ('subckt', 'model', 'native_primitive', 'none')",
            name="ck_part_spice_bindings_model_kind",
        ),
        sa.CheckConstraint(
            "model_kind NOT IN ('subckt', 'model') "
            "OR (subckt IS NOT NULL AND model_sha256 IS NOT NULL)",
            name="ck_part_spice_bindings_file_model",
        ),
    )
    op.create_index(
        "ix_part_spice_bindings_workspace_id", "part_spice_bindings", ["workspace_id"]
    )
    op.create_index(
        "ix_part_spice_bindings_archived_at", "part_spice_bindings", ["archived_at"]
    )
    op.create_index(
        "ix_part_spice_bindings_ws_part",
        "part_spice_bindings",
        ["workspace_id", "part_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_part_spice_bindings_ws_part", table_name="part_spice_bindings")
    op.drop_index("ix_part_spice_bindings_archived_at", table_name="part_spice_bindings")
    op.drop_index("ix_part_spice_bindings_workspace_id", table_name="part_spice_bindings")
    op.drop_table("part_spice_bindings")
