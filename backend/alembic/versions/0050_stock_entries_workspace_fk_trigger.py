"""Enforce stock_entries foreign-key workspace isolation.

Revision ID: 0050
Revises: 0049
Create Date: 2026-05-14

AUD-052 / issue #591.

The service layer verifies workspace ownership before inserting ledger rows.
This trigger provides the same defense at the database boundary so direct SQL
cannot attach a stock_entries row to objects owned by another workspace.
"""

from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION check_stock_entries_workspace_fks()
RETURNS trigger AS $$
BEGIN
  PERFORM 1 FROM parts
   WHERE id = NEW.part_id
     AND workspace_id = NEW.workspace_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'stock_entries.part_id (%) not in workspace (%)',
      NEW.part_id, NEW.workspace_id
      USING ERRCODE = '23514';
  END IF;

  IF NEW.lot_id IS NOT NULL THEN
    PERFORM 1 FROM lots
     WHERE id = NEW.lot_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stock_entries.lot_id (%) not in workspace (%)',
        NEW.lot_id, NEW.workspace_id
        USING ERRCODE = '23514';
    END IF;
  END IF;

  IF NEW.storage_location_id IS NOT NULL THEN
    PERFORM 1 FROM storage_locations
     WHERE id = NEW.storage_location_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stock_entries.storage_location_id (%) not in workspace (%)',
        NEW.storage_location_id, NEW.workspace_id
        USING ERRCODE = '23514';
    END IF;
  END IF;

  IF NEW.related_entry_id IS NOT NULL THEN
    PERFORM 1 FROM stock_entries
     WHERE id = NEW.related_entry_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stock_entries.related_entry_id (%) not in workspace (%)',
        NEW.related_entry_id, NEW.workspace_id
        USING ERRCODE = '23514';
    END IF;
  END IF;

  IF NEW.order_id IS NOT NULL THEN
    PERFORM 1 FROM orders
     WHERE id = NEW.order_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stock_entries.order_id (%) not in workspace (%)',
        NEW.order_id, NEW.workspace_id
        USING ERRCODE = '23514';
    END IF;
  END IF;

  IF NEW.order_entry_id IS NOT NULL THEN
    PERFORM 1 FROM order_entries
     WHERE id = NEW.order_entry_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stock_entries.order_entry_id (%) not in workspace (%)',
        NEW.order_entry_id, NEW.workspace_id
        USING ERRCODE = '23514';
    END IF;
  END IF;

  IF NEW.project_id IS NOT NULL THEN
    PERFORM 1 FROM projects
     WHERE id = NEW.project_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stock_entries.project_id (%) not in workspace (%)',
        NEW.project_id, NEW.workspace_id
        USING ERRCODE = '23514';
    END IF;
  END IF;

  IF NEW.build_id IS NOT NULL THEN
    PERFORM 1 FROM builds
     WHERE id = NEW.build_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stock_entries.build_id (%) not in workspace (%)',
        NEW.build_id, NEW.workspace_id
        USING ERRCODE = '23514';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(_FUNCTION_SQL)
    op.execute("""
    CREATE TRIGGER stock_entries_workspace_fk_check
      BEFORE INSERT OR UPDATE OF
        workspace_id,
        part_id,
        lot_id,
        storage_location_id,
        related_entry_id,
        order_id,
        order_entry_id,
        project_id,
        build_id
      ON stock_entries
      FOR EACH ROW
      EXECUTE FUNCTION check_stock_entries_workspace_fks();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS stock_entries_workspace_fk_check ON stock_entries;")
    op.execute("DROP FUNCTION IF EXISTS check_stock_entries_workspace_fks();")
