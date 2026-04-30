"""parts mpn unique per workspace (partial unique index)

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-30

The user wants `Each MPN can have only one part` per workspace. The
existing `ix_parts_ws_mpn` was a non-unique composite on
(workspace_id, manufacturer, mpn) — too permissive. Replace with a
partial unique index that ignores rows where mpn IS NULL (so any
number of mpn-less parts can coexist).
"""
from alembic import op


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old composite scan index — the new partial unique covers
    # the workspace+mpn lookup just as well.
    op.drop_index("ix_parts_ws_mpn", table_name="parts")
    # Partial predicate excludes:
    #   - rows with NULL mpn (manual / sub-assembly parts)
    #   - archived rows (archiving frees up the MPN for re-use)
    op.create_index(
        "uq_parts_ws_mpn",
        "parts",
        ["workspace_id", "mpn"],
        unique=True,
        postgresql_where="mpn IS NOT NULL AND archived_at IS NULL",
    )


def downgrade() -> None:
    op.drop_index("uq_parts_ws_mpn", table_name="parts")
    op.create_index(
        "ix_parts_ws_mpn",
        "parts",
        ["workspace_id", "manufacturer", "mpn"],
        unique=False,
    )
