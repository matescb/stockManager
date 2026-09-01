"""Add the EDA domain: symbols, footprints, data files, and part_eda.

Five additive tables, no changes to anything existing.

No BEFORE triggers in this migration. `parts.category_id` got one in
0067 because raw SQL — migrations, admin queries — writes that column
routinely and a foreign category would be invisible. Nothing writes
these five tables outside the service, whose every cross-table lookup
goes through `assert_in_workspace`, and the cost of five more trigger
functions is real. `tests/test_eda.py` pins the app-layer guards
instead; if a later phase starts backfilling these tables from SQL,
add the triggers then.

Revision ID: 0068
Revises: 0067
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def _workspace_owned_columns() -> list[sa.Column]:
    """The `WorkspaceOwned` mixin's columns, spelled out.

    Repeated verbatim across four tables here. The mixin lives in
    `app.domain._mixins`, which a migration must not import
    (`tests/test_migration_isolation.py`) — a migration has to keep
    describing the schema as it was on the day it ran, not as the model
    later becomes.
    """
    return [
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    ]


def _workspace_owned_constraints() -> list:
    return [
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    ]


def _library_indexes(table: str) -> None:
    op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])
    op.create_index(f"ix_{table}_archived_at", table, ["archived_at"])
    op.create_index(f"ix_{table}_ws_archived", table, ["workspace_id", "archived_at"])


def upgrade() -> None:
    # -- eda_symbols / eda_footprints -----------------------------------
    # Identical shape; `name` is the KiCad entry name and is unique per
    # workspace among ACTIVE rows only, so archiving frees it for re-use
    # (same shape as `uq_part_categories_ws_name`, alembic 0067).
    for table in ("eda_symbols", "eda_footprints"):
        op.create_table(
            table,
            *_workspace_owned_columns(),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column(
                "source",
                sa.String(length=20),
                nullable=False,
                server_default=sa.text("'manual'"),
            ),
            sa.Column("category_id", sa.UUID(as_uuid=True), nullable=True),
            *_workspace_owned_constraints(),
            sa.ForeignKeyConstraint(
                ["category_id"], ["part_categories.id"], ondelete="SET NULL"
            ),
        )
        _library_indexes(table)
        op.create_index(
            f"uq_{table}_ws_name",
            table,
            ["workspace_id", "name"],
            unique=True,
            postgresql_where=sa.text("archived_at IS NULL"),
        )

    # -- eda_datafiles --------------------------------------------------
    # 3D models (step / wrl) and SPICE models share one table; `kind`
    # joins the unique key so a "R_0402.step" and a "R_0402.spice" can
    # both exist.
    op.create_table(
        "eda_datafiles",
        *_workspace_owned_columns(),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
        *_workspace_owned_constraints(),
    )
    _library_indexes("eda_datafiles")
    op.create_index(
        "uq_eda_datafiles_ws_kind_name",
        "eda_datafiles",
        ["workspace_id", "kind", "name"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )

    # -- eda_footprint_models -------------------------------------------
    # Join row, so no archive / authorship columns. CASCADE on both
    # parents: a link to a deleted footprint or model is meaningless.
    op.create_table(
        "eda_footprint_models",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("footprint_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("datafile_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["footprint_id"], ["eda_footprints.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["datafile_id"], ["eda_datafiles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("footprint_id", "datafile_id", name="uq_eda_footprint_model"),
    )
    op.create_index(
        "ix_eda_footprint_models_workspace_id", "eda_footprint_models", ["workspace_id"]
    )
    op.create_index(
        "ix_eda_footprint_models_footprint_id", "eda_footprint_models", ["footprint_id"]
    )
    op.create_index(
        "ix_eda_footprint_models_datafile_id", "eda_footprint_models", ["datafile_id"]
    )
    op.create_index(
        "ix_eda_footprint_models_ws_footprint",
        "eda_footprint_models",
        ["workspace_id", "footprint_id"],
    )

    # -- part_eda -------------------------------------------------------
    # 1:1 with parts (UNIQUE on part_id). SET NULL on the three library
    # FKs: hard-deleting a symbol must clear the reference, never take
    # the whole configuration with it.
    op.create_table(
        "part_eda",
        *_workspace_owned_columns(),
        sa.Column("part_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("symbol_ref_external", sa.String(length=200), nullable=True),
        sa.Column("footprint_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("footprint_ref_external", sa.String(length=200), nullable=True),
        sa.Column("spice_datafile_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("value", sa.String(length=120), nullable=True),
        sa.Column("keywords", sa.String(length=300), nullable=True),
        sa.Column(
            "footprint_filters",
            postgresql.ARRAY(sa.String(length=100)),
            nullable=True,
        ),
        sa.Column(
            "exclude_from_bom",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "exclude_from_board",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "exclude_from_sim",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("sim_device", sa.String(length=60), nullable=True),
        sa.Column("sim_pins", sa.String(length=300), nullable=True),
        sa.Column("sim_params", sa.String(length=500), nullable=True),
        *_workspace_owned_constraints(),
        sa.ForeignKeyConstraint(["part_id"], ["parts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["symbol_id"], ["eda_symbols.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["footprint_id"], ["eda_footprints.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["spice_datafile_id"], ["eda_datafiles.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("part_id", name="uq_part_eda_part"),
        # A slot names EITHER a definition we host OR one in the user's
        # local libraries — never both. Enforced here as well as in the
        # service so a future SQL backfill can't produce a row whose
        # symbol resolves two ways.
        sa.CheckConstraint(
            "NOT (symbol_id IS NOT NULL AND symbol_ref_external IS NOT NULL)",
            name="ck_part_eda_symbol_ref_exclusive",
        ),
        sa.CheckConstraint(
            "NOT (footprint_id IS NOT NULL AND footprint_ref_external IS NOT NULL)",
            name="ck_part_eda_footprint_ref_exclusive",
        ),
    )
    op.create_index("ix_part_eda_workspace_id", "part_eda", ["workspace_id"])
    op.create_index("ix_part_eda_archived_at", "part_eda", ["archived_at"])
    op.create_index("ix_part_eda_part_id", "part_eda", ["part_id"])
    op.create_index("ix_part_eda_ws_part", "part_eda", ["workspace_id", "part_id"])


def downgrade() -> None:
    # Reverse dependency order: part_eda and the join table reference the
    # three library tables.
    op.drop_index("ix_part_eda_ws_part", table_name="part_eda")
    op.drop_index("ix_part_eda_part_id", table_name="part_eda")
    op.drop_index("ix_part_eda_archived_at", table_name="part_eda")
    op.drop_index("ix_part_eda_workspace_id", table_name="part_eda")
    op.drop_table("part_eda")

    op.drop_index(
        "ix_eda_footprint_models_ws_footprint", table_name="eda_footprint_models"
    )
    op.drop_index(
        "ix_eda_footprint_models_datafile_id", table_name="eda_footprint_models"
    )
    op.drop_index(
        "ix_eda_footprint_models_footprint_id", table_name="eda_footprint_models"
    )
    op.drop_index(
        "ix_eda_footprint_models_workspace_id", table_name="eda_footprint_models"
    )
    op.drop_table("eda_footprint_models")

    op.drop_index("uq_eda_datafiles_ws_kind_name", table_name="eda_datafiles")
    op.drop_index("ix_eda_datafiles_ws_archived", table_name="eda_datafiles")
    op.drop_index("ix_eda_datafiles_archived_at", table_name="eda_datafiles")
    op.drop_index("ix_eda_datafiles_workspace_id", table_name="eda_datafiles")
    op.drop_table("eda_datafiles")

    for table in ("eda_footprints", "eda_symbols"):
        op.drop_index(f"uq_{table}_ws_name", table_name=table)
        op.drop_index(f"ix_{table}_ws_archived", table_name=table)
        op.drop_index(f"ix_{table}_archived_at", table_name=table)
        op.drop_index(f"ix_{table}_workspace_id", table_name=table)
        op.drop_table(table)
