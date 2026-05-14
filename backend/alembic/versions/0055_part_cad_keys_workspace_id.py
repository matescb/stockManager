"""Add workspace_id to part_cad_keys.

Revision ID: 0055
Revises: 0054
Create Date: 2026-05-14

AUD-054 / issue #593.

part_cad_keys are part-owned, but BOM matching is workspace-sensitive. Store
the workspace directly for grep-able query isolation and enforce that it stays
aligned with the owning part at the database boundary.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION check_part_cad_keys_workspace()
RETURNS trigger AS $$
BEGIN
  PERFORM 1 FROM parts
   WHERE id = NEW.part_id
     AND workspace_id = NEW.workspace_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'part_cad_keys.part_id (%) not in workspace (%)',
      NEW.part_id, NEW.workspace_id
      USING ERRCODE = '23514';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.add_column(
        "part_cad_keys",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        """
        UPDATE part_cad_keys pck
           SET workspace_id = p.workspace_id
          FROM parts p
         WHERE pck.part_id = p.id
        """
    )
    op.alter_column("part_cad_keys", "workspace_id", nullable=False)
    op.create_foreign_key(
        "fk_part_cad_keys_workspace_id_workspaces",
        "part_cad_keys",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_part_cad_keys_workspace_id"),
        "part_cad_keys",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_part_cad_keys_ws_cad_key",
        "part_cad_keys",
        ["workspace_id", "cad_key"],
        unique=False,
    )
    op.execute(_FUNCTION_SQL)
    op.execute(
        """
        CREATE TRIGGER part_cad_keys_workspace_check
          BEFORE INSERT OR UPDATE OF workspace_id, part_id
          ON part_cad_keys
          FOR EACH ROW
          EXECUTE FUNCTION check_part_cad_keys_workspace();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS part_cad_keys_workspace_check ON part_cad_keys;")
    op.execute("DROP FUNCTION IF EXISTS check_part_cad_keys_workspace();")
    op.drop_index("ix_part_cad_keys_ws_cad_key", table_name="part_cad_keys")
    op.drop_index(op.f("ix_part_cad_keys_workspace_id"), table_name="part_cad_keys")
    op.drop_constraint(
        "fk_part_cad_keys_workspace_id_workspaces",
        "part_cad_keys",
        type_="foreignkey",
    )
    op.drop_column("part_cad_keys", "workspace_id")
