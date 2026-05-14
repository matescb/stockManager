"""Add workspace columns and triggers to part link tables.

Revision ID: 0054
Revises: 0053
Create Date: 2026-05-14

AUD-051 / issue #590.

part_substitutes and part_meta_members only referenced parts by UUID. The
application checked workspace ownership, but direct SQL could still create a
cross-workspace link. Store the owning workspace explicitly and enforce that
all referenced parts belong to it.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


_PART_SUBSTITUTES_FUNCTION = """
CREATE OR REPLACE FUNCTION check_part_substitutes_workspace_fks()
RETURNS trigger AS $$
BEGIN
  PERFORM 1 FROM parts
   WHERE id = NEW.part_id
     AND workspace_id = NEW.workspace_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'part_substitutes.part_id (%) not in workspace (%)',
      NEW.part_id, NEW.workspace_id
      USING ERRCODE = '23514';
  END IF;

  PERFORM 1 FROM parts
   WHERE id = NEW.substitute_part_id
     AND workspace_id = NEW.workspace_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'part_substitutes.substitute_part_id (%) not in workspace (%)',
      NEW.substitute_part_id, NEW.workspace_id
      USING ERRCODE = '23514';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


_PART_META_MEMBERS_FUNCTION = """
CREATE OR REPLACE FUNCTION check_part_meta_members_workspace_fks()
RETURNS trigger AS $$
BEGIN
  PERFORM 1 FROM parts
   WHERE id = NEW.meta_part_id
     AND workspace_id = NEW.workspace_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'part_meta_members.meta_part_id (%) not in workspace (%)',
      NEW.meta_part_id, NEW.workspace_id
      USING ERRCODE = '23514';
  END IF;

  PERFORM 1 FROM parts
   WHERE id = NEW.part_id
     AND workspace_id = NEW.workspace_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'part_meta_members.part_id (%) not in workspace (%)',
      NEW.part_id, NEW.workspace_id
      USING ERRCODE = '23514';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    uuid_type = postgresql.UUID(as_uuid=True)

    op.add_column("part_substitutes", sa.Column("workspace_id", uuid_type, nullable=True))
    op.add_column("part_meta_members", sa.Column("workspace_id", uuid_type, nullable=True))

    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (
        SELECT 1
        FROM part_substitutes ps
        JOIN parts p ON p.id = ps.part_id
        JOIN parts sub ON sub.id = ps.substitute_part_id
        WHERE p.workspace_id <> sub.workspace_id
      ) THEN
        RAISE EXCEPTION 'part_substitutes contains cross-workspace rows'
          USING ERRCODE = '23514';
      END IF;

      IF EXISTS (
        SELECT 1
        FROM part_meta_members pmm
        JOIN parts meta ON meta.id = pmm.meta_part_id
        JOIN parts member ON member.id = pmm.part_id
        WHERE meta.workspace_id <> member.workspace_id
      ) THEN
        RAISE EXCEPTION 'part_meta_members contains cross-workspace rows'
          USING ERRCODE = '23514';
      END IF;
    END
    $$;
    """)

    op.execute("""
    UPDATE part_substitutes ps
       SET workspace_id = p.workspace_id
      FROM parts p
     WHERE p.id = ps.part_id
    """)
    op.execute("""
    UPDATE part_meta_members pmm
       SET workspace_id = p.workspace_id
      FROM parts p
     WHERE p.id = pmm.meta_part_id
    """)

    op.alter_column("part_substitutes", "workspace_id", nullable=False)
    op.alter_column("part_meta_members", "workspace_id", nullable=False)

    op.create_foreign_key(
        "fk_part_substitutes_workspace_id",
        "part_substitutes",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_part_meta_members_workspace_id",
        "part_meta_members",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.create_index(
        "ix_part_substitutes_workspace_id",
        "part_substitutes",
        ["workspace_id"],
    )
    op.create_index(
        "ix_part_substitutes_ws_part",
        "part_substitutes",
        ["workspace_id", "part_id"],
    )
    op.create_index(
        "ix_part_meta_members_workspace_id",
        "part_meta_members",
        ["workspace_id"],
    )
    op.create_index(
        "ix_part_meta_members_ws_meta",
        "part_meta_members",
        ["workspace_id", "meta_part_id"],
    )

    op.execute(_PART_SUBSTITUTES_FUNCTION)
    op.execute("""
    CREATE TRIGGER part_substitutes_workspace_fk_check
      BEFORE INSERT OR UPDATE OF workspace_id, part_id, substitute_part_id
      ON part_substitutes
      FOR EACH ROW
      EXECUTE FUNCTION check_part_substitutes_workspace_fks();
    """)
    op.execute(_PART_META_MEMBERS_FUNCTION)
    op.execute("""
    CREATE TRIGGER part_meta_members_workspace_fk_check
      BEFORE INSERT OR UPDATE OF workspace_id, meta_part_id, part_id
      ON part_meta_members
      FOR EACH ROW
      EXECUTE FUNCTION check_part_meta_members_workspace_fks();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS part_meta_members_workspace_fk_check ON part_meta_members;")
    op.execute("DROP FUNCTION IF EXISTS check_part_meta_members_workspace_fks();")
    op.execute("DROP TRIGGER IF EXISTS part_substitutes_workspace_fk_check ON part_substitutes;")
    op.execute("DROP FUNCTION IF EXISTS check_part_substitutes_workspace_fks();")

    op.drop_index("ix_part_meta_members_ws_meta", table_name="part_meta_members")
    op.drop_index("ix_part_meta_members_workspace_id", table_name="part_meta_members")
    op.drop_index("ix_part_substitutes_ws_part", table_name="part_substitutes")
    op.drop_index("ix_part_substitutes_workspace_id", table_name="part_substitutes")

    op.drop_constraint(
        "fk_part_meta_members_workspace_id",
        "part_meta_members",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_part_substitutes_workspace_id",
        "part_substitutes",
        type_="foreignkey",
    )

    op.drop_column("part_meta_members", "workspace_id")
    op.drop_column("part_substitutes", "workspace_id")
