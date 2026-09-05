"""Add build_stages / build_stage_lines and stock_entries.build_stage_id.

Revision ID: 0076
Revises: 0075
Create Date: 2026-09-05

Track B2 — multi-stage builds. A build may be assembled across several
stages, each consuming a defined subset (and portion) of the project BOM,
so a partially-built device is tracked accurately and stock is drawn down
progressively instead of in one all-at-once consume.

Shape: two tables hanging off the ``Build`` aggregate.

* ``build_stages`` — name, sequence, status per stage of ONE build. Stages
  belong to the build, not the project: two builds of the same project can
  be staged differently, and a stage's status describes this physical pass.
* ``build_stage_lines`` — the (stage, project_entry) pairs plus
  ``portion_pct``, the percentage of the BOM line's whole-build requirement
  that this stage takes. Percentages (not absolute quantities) keep the
  attrition-adjusted integer from ``builds/service.py::_required`` the one
  source of truth; stage quantities are allocated from it.

``stock_entries.build_stage_id`` tags the ledger rows a per-stage consume
writes so the trail shows what each stage took. It is NULL for every row a
single-pass build emits, and ``ON DELETE SET NULL`` like ``build_id`` so a
hard-deleted build never deletes independent stock history (ADR-0028).

Defence-in-depth workspace triggers mirror 0064's contract: validate every
parent ref on INSERT, only changed refs on UPDATE, and raise SQLSTATE
``WS001`` so ``raise_integrity_as_409`` maps a violation to a 409.

Numbering: 0073 (object codes) is merged; 0074 (uom widening) and 0075
(label templates) are sibling PRs in flight. This revision chains after
0075 for a single linear head.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


_BUILD_STAGES_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION check_build_stages_workspace_fks()
RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'INSERT' OR NEW.build_id IS DISTINCT FROM OLD.build_id
     OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id THEN
    PERFORM 1 FROM builds
     WHERE id = NEW.build_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'build_stages.build_id (%) not in workspace (%)',
        NEW.build_id, NEW.workspace_id
        USING ERRCODE = 'WS001';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_BUILD_STAGE_LINES_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION check_build_stage_lines_workspace_fks()
RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'INSERT' OR NEW.build_stage_id IS DISTINCT FROM OLD.build_stage_id
     OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id THEN
    PERFORM 1 FROM build_stages
     WHERE id = NEW.build_stage_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'build_stage_lines.build_stage_id (%) not in workspace (%)',
        NEW.build_stage_id, NEW.workspace_id
        USING ERRCODE = 'WS001';
    END IF;
  END IF;

  IF TG_OP = 'INSERT' OR NEW.project_entry_id IS DISTINCT FROM OLD.project_entry_id
     OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id THEN
    PERFORM 1 FROM project_entries
     WHERE id = NEW.project_entry_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'build_stage_lines.project_entry_id (%) not in workspace (%)',
        NEW.project_entry_id, NEW.workspace_id
        USING ERRCODE = 'WS001';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# Separate, additive trigger rather than a rewrite of 0064's
# check_stock_entries_workspace_fks(): re-emitting that ~200-line function
# body just to append one branch would make this migration's downgrade
# responsible for restoring it verbatim. A dedicated trigger for the one
# new column is the same guarantee with a downgrade that only has to drop
# what it created (same pattern as 0067's parts_category_workspace_check).
_STOCK_ENTRIES_BUILD_STAGE_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION check_stock_entries_build_stage_workspace()
RETURNS trigger AS $$
BEGIN
  IF NEW.build_stage_id IS NOT NULL
     AND (TG_OP = 'INSERT'
          OR NEW.build_stage_id IS DISTINCT FROM OLD.build_stage_id
          OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) THEN
    PERFORM 1 FROM build_stages
     WHERE id = NEW.build_stage_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stock_entries.build_stage_id (%) not in workspace (%)',
        NEW.build_stage_id, NEW.workspace_id
        USING ERRCODE = 'WS001';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "build_stages",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("build_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'planned'"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('planned', 'in_progress', 'complete')",
            name="ck_build_stages_status",
        ),
        sa.CheckConstraint("sequence >= 0", name="ck_build_stages_sequence_nonneg"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        # CASCADE: a deleted build takes its stages with it. The ledger rows
        # keep their history via SET NULL on stock_entries.build_stage_id.
        sa.ForeignKeyConstraint(["build_id"], ["builds.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_build_stages_workspace_id", "build_stages", ["workspace_id"])
    op.create_index("ix_build_stages_archived_at", "build_stages", ["archived_at"])
    op.create_index("ix_build_stages_ws_build", "build_stages", ["workspace_id", "build_id"])
    op.create_index(
        "ix_build_stages_ws_archived", "build_stages", ["workspace_id", "archived_at"]
    )
    # Active rows only: archiving a stage frees its sequence number for reuse
    # (same shape as uq_part_categories_ws_name, alembic 0067).
    op.create_index(
        "uq_build_stages_build_sequence",
        "build_stages",
        ["build_id", "sequence"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )

    op.create_table(
        "build_stage_lines",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("build_stage_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("project_entry_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "portion_pct",
            sa.Numeric(7, 4),
            nullable=False,
            server_default="100",
        ),
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
        sa.CheckConstraint(
            "portion_pct > 0 AND portion_pct <= 100",
            name="ck_build_stage_lines_portion_range",
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["build_stage_id"], ["build_stages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["project_entry_id"], ["project_entries.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_build_stage_lines_workspace_id", "build_stage_lines", ["workspace_id"]
    )
    op.create_index("ix_build_stage_lines_archived_at", "build_stage_lines", ["archived_at"])
    op.create_index(
        "ix_build_stage_lines_ws_stage",
        "build_stage_lines",
        ["workspace_id", "build_stage_id"],
    )
    op.create_index(
        "ix_build_stage_lines_ws_entry",
        "build_stage_lines",
        ["workspace_id", "project_entry_id"],
    )
    # One row per (stage, BOM line): a stage takes one portion of a line, not
    # several. Re-staging the same line means updating the portion.
    op.create_index(
        "uq_build_stage_lines_stage_entry",
        "build_stage_lines",
        ["build_stage_id", "project_entry_id"],
        unique=True,
    )

    op.add_column(
        "stock_entries",
        sa.Column("build_stage_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_stock_entries_build_stage_id",
        "stock_entries",
        "build_stages",
        ["build_stage_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Partial: only per-stage consume rows populate the column, so the index
    # skips the ~100% NULL majority of the ledger (same rationale as
    # ix_stock_ws_bag_signature, alembic 0019).
    op.create_index(
        "ix_stock_ws_build_stage",
        "stock_entries",
        ["workspace_id", "build_stage_id"],
        postgresql_where=sa.text("build_stage_id IS NOT NULL"),
    )

    op.execute(_BUILD_STAGES_TRIGGER_FN)
    op.execute("""
    CREATE TRIGGER build_stages_workspace_fk_check
      BEFORE INSERT OR UPDATE OF workspace_id, build_id
      ON build_stages
      FOR EACH ROW
      EXECUTE FUNCTION check_build_stages_workspace_fks();
    """)

    op.execute(_BUILD_STAGE_LINES_TRIGGER_FN)
    op.execute("""
    CREATE TRIGGER build_stage_lines_workspace_fk_check
      BEFORE INSERT OR UPDATE OF workspace_id, build_stage_id, project_entry_id
      ON build_stage_lines
      FOR EACH ROW
      EXECUTE FUNCTION check_build_stage_lines_workspace_fks();
    """)

    op.execute(_STOCK_ENTRIES_BUILD_STAGE_TRIGGER_FN)
    op.execute("""
    CREATE TRIGGER stock_entries_build_stage_workspace_check
      BEFORE INSERT OR UPDATE OF workspace_id, build_stage_id
      ON stock_entries
      FOR EACH ROW
      EXECUTE FUNCTION check_stock_entries_build_stage_workspace();
    """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS stock_entries_build_stage_workspace_check ON stock_entries;"
    )
    op.execute("DROP FUNCTION IF EXISTS check_stock_entries_build_stage_workspace();")
    op.execute(
        "DROP TRIGGER IF EXISTS build_stage_lines_workspace_fk_check ON build_stage_lines;"
    )
    op.execute("DROP FUNCTION IF EXISTS check_build_stage_lines_workspace_fks();")
    op.execute("DROP TRIGGER IF EXISTS build_stages_workspace_fk_check ON build_stages;")
    op.execute("DROP FUNCTION IF EXISTS check_build_stages_workspace_fks();")

    # Drop the referencing column before the tables it points at.
    op.drop_index("ix_stock_ws_build_stage", table_name="stock_entries")
    op.drop_constraint("fk_stock_entries_build_stage_id", "stock_entries", type_="foreignkey")
    op.drop_column("stock_entries", "build_stage_id")

    op.drop_index("uq_build_stage_lines_stage_entry", table_name="build_stage_lines")
    op.drop_index("ix_build_stage_lines_ws_entry", table_name="build_stage_lines")
    op.drop_index("ix_build_stage_lines_ws_stage", table_name="build_stage_lines")
    op.drop_index("ix_build_stage_lines_archived_at", table_name="build_stage_lines")
    op.drop_index("ix_build_stage_lines_workspace_id", table_name="build_stage_lines")
    op.drop_table("build_stage_lines")

    op.drop_index("uq_build_stages_build_sequence", table_name="build_stages")
    op.drop_index("ix_build_stages_ws_archived", table_name="build_stages")
    op.drop_index("ix_build_stages_ws_build", table_name="build_stages")
    op.drop_index("ix_build_stages_archived_at", table_name="build_stages")
    op.drop_index("ix_build_stages_workspace_id", table_name="build_stages")
    op.drop_table("build_stages")
