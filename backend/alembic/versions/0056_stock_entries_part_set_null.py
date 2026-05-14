"""Preserve stock ledger rows when parts are deleted.

Revision ID: 0056
Revises: 0055
Create Date: 2026-05-14

AUD-018 / issue #557.

`stock_entries` is append-only audit history. A hard delete of a part must not
cascade-delete that history; it should detach the row by nulling `part_id`.
"""

from __future__ import annotations

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


_WORKSPACE_FK_TRIGGER_SET_NULL_SQL = """
CREATE OR REPLACE FUNCTION check_stock_entries_workspace_fks()
RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'INSERT' AND NEW.part_id IS NULL THEN
    RAISE EXCEPTION 'stock_entries.part_id is required on insert'
      USING ERRCODE = '23514';
  END IF;

  IF NEW.part_id IS NOT NULL THEN
    PERFORM 1 FROM parts
     WHERE id = NEW.part_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stock_entries.part_id (%) not in workspace (%)',
        NEW.part_id, NEW.workspace_id
        USING ERRCODE = '23514';
    END IF;
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


_WORKSPACE_FK_TRIGGER_CASCADE_SQL = """
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
    op.execute("ALTER TABLE stock_entries DROP CONSTRAINT IF EXISTS stock_entries_part_id_fkey")
    op.execute("ALTER TABLE stock_entries ALTER COLUMN part_id DROP NOT NULL")
    op.execute(
        """
        ALTER TABLE stock_entries
        ADD CONSTRAINT stock_entries_part_id_fkey
        FOREIGN KEY (part_id) REFERENCES parts(id)
        ON DELETE SET NULL
        NOT VALID
        """
    )
    op.execute("ALTER TABLE stock_entries VALIDATE CONSTRAINT stock_entries_part_id_fkey")
    op.execute(_WORKSPACE_FK_TRIGGER_SET_NULL_SQL)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM stock_entries WHERE part_id IS NULL) THEN
            RAISE EXCEPTION
              'cannot downgrade 0055 while stock_entries rows have NULL part_id';
          END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE stock_entries DROP CONSTRAINT IF EXISTS stock_entries_part_id_fkey")
    op.execute("ALTER TABLE stock_entries ALTER COLUMN part_id SET NOT NULL")
    op.execute(
        """
        ALTER TABLE stock_entries
        ADD CONSTRAINT stock_entries_part_id_fkey
        FOREIGN KEY (part_id) REFERENCES parts(id)
        ON DELETE CASCADE
        NOT VALID
        """
    )
    op.execute("ALTER TABLE stock_entries VALIDATE CONSTRAINT stock_entries_part_id_fkey")
    op.execute(_WORKSPACE_FK_TRIGGER_CASCADE_SQL)
