"""Preserve lots on part delete and gate workspace trigger updates.

Revision ID: 0058
Revises: 0057
Create Date: 2026-05-15

AUD-072 / issue #710.

`lots` are historical receiving/serial records and should survive a hard part
delete just like `stock_entries`. The workspace FK triggers remain strict on
INSERT, but UPDATEs only re-check a reference when that reference column
changes.
"""

from __future__ import annotations

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


_STOCK_ENTRIES_UPDATE_GATED_SQL = """
CREATE OR REPLACE FUNCTION check_stock_entries_workspace_fks()
RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.part_id IS NULL THEN
      RAISE EXCEPTION 'stock_entries.part_id is required on insert'
        USING ERRCODE = '23514';
    END IF;

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
  END IF;

  IF NEW.part_id IS DISTINCT FROM OLD.part_id AND NEW.part_id IS NOT NULL THEN
    PERFORM 1 FROM parts
     WHERE id = NEW.part_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stock_entries.part_id (%) not in workspace (%)',
        NEW.part_id, NEW.workspace_id
        USING ERRCODE = '23514';
    END IF;
  END IF;

  IF NEW.lot_id IS DISTINCT FROM OLD.lot_id AND NEW.lot_id IS NOT NULL THEN
    PERFORM 1 FROM lots
     WHERE id = NEW.lot_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stock_entries.lot_id (%) not in workspace (%)',
        NEW.lot_id, NEW.workspace_id
        USING ERRCODE = '23514';
    END IF;
  END IF;

  IF NEW.storage_location_id IS DISTINCT FROM OLD.storage_location_id
     AND NEW.storage_location_id IS NOT NULL THEN
    PERFORM 1 FROM storage_locations
     WHERE id = NEW.storage_location_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stock_entries.storage_location_id (%) not in workspace (%)',
        NEW.storage_location_id, NEW.workspace_id
        USING ERRCODE = '23514';
    END IF;
  END IF;

  IF NEW.related_entry_id IS DISTINCT FROM OLD.related_entry_id
     AND NEW.related_entry_id IS NOT NULL THEN
    PERFORM 1 FROM stock_entries
     WHERE id = NEW.related_entry_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stock_entries.related_entry_id (%) not in workspace (%)',
        NEW.related_entry_id, NEW.workspace_id
        USING ERRCODE = '23514';
    END IF;
  END IF;

  IF NEW.order_id IS DISTINCT FROM OLD.order_id AND NEW.order_id IS NOT NULL THEN
    PERFORM 1 FROM orders
     WHERE id = NEW.order_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stock_entries.order_id (%) not in workspace (%)',
        NEW.order_id, NEW.workspace_id
        USING ERRCODE = '23514';
    END IF;
  END IF;

  IF NEW.order_entry_id IS DISTINCT FROM OLD.order_entry_id
     AND NEW.order_entry_id IS NOT NULL THEN
    PERFORM 1 FROM order_entries
     WHERE id = NEW.order_entry_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stock_entries.order_entry_id (%) not in workspace (%)',
        NEW.order_entry_id, NEW.workspace_id
        USING ERRCODE = '23514';
    END IF;
  END IF;

  IF NEW.project_id IS DISTINCT FROM OLD.project_id AND NEW.project_id IS NOT NULL THEN
    PERFORM 1 FROM projects
     WHERE id = NEW.project_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stock_entries.project_id (%) not in workspace (%)',
        NEW.project_id, NEW.workspace_id
        USING ERRCODE = '23514';
    END IF;
  END IF;

  IF NEW.build_id IS DISTINCT FROM OLD.build_id AND NEW.build_id IS NOT NULL THEN
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


_STOCK_ENTRIES_FULL_RECHECK_SQL = """
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


_PART_SUBSTITUTES_UPDATE_GATED_SQL = """
CREATE OR REPLACE FUNCTION check_part_substitutes_workspace_fks()
RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
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
  END IF;

  IF NEW.part_id IS DISTINCT FROM OLD.part_id THEN
    PERFORM 1 FROM parts
     WHERE id = NEW.part_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'part_substitutes.part_id (%) not in workspace (%)',
        NEW.part_id, NEW.workspace_id
        USING ERRCODE = '23514';
    END IF;
  END IF;

  IF NEW.substitute_part_id IS DISTINCT FROM OLD.substitute_part_id THEN
    PERFORM 1 FROM parts
     WHERE id = NEW.substitute_part_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'part_substitutes.substitute_part_id (%) not in workspace (%)',
        NEW.substitute_part_id, NEW.workspace_id
        USING ERRCODE = '23514';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


_PART_SUBSTITUTES_FULL_RECHECK_SQL = """
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


_PART_META_MEMBERS_UPDATE_GATED_SQL = """
CREATE OR REPLACE FUNCTION check_part_meta_members_workspace_fks()
RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
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
  END IF;

  IF NEW.meta_part_id IS DISTINCT FROM OLD.meta_part_id THEN
    PERFORM 1 FROM parts
     WHERE id = NEW.meta_part_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'part_meta_members.meta_part_id (%) not in workspace (%)',
        NEW.meta_part_id, NEW.workspace_id
        USING ERRCODE = '23514';
    END IF;
  END IF;

  IF NEW.part_id IS DISTINCT FROM OLD.part_id THEN
    PERFORM 1 FROM parts
     WHERE id = NEW.part_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'part_meta_members.part_id (%) not in workspace (%)',
        NEW.part_id, NEW.workspace_id
        USING ERRCODE = '23514';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


_PART_META_MEMBERS_FULL_RECHECK_SQL = """
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


_PART_CAD_KEYS_UPDATE_GATED_SQL = """
CREATE OR REPLACE FUNCTION check_part_cad_keys_workspace()
RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    PERFORM 1 FROM parts
     WHERE id = NEW.part_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'part_cad_keys.part_id (%) not in workspace (%)',
        NEW.part_id, NEW.workspace_id
        USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
  END IF;

  IF NEW.part_id IS DISTINCT FROM OLD.part_id THEN
    PERFORM 1 FROM parts
     WHERE id = NEW.part_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'part_cad_keys.part_id (%) not in workspace (%)',
        NEW.part_id, NEW.workspace_id
        USING ERRCODE = '23514';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


_PART_CAD_KEYS_FULL_RECHECK_SQL = """
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


def _set_lots_part_set_null() -> None:
    op.execute("ALTER TABLE lots DROP CONSTRAINT IF EXISTS lots_part_id_fkey")
    op.execute("ALTER TABLE lots ALTER COLUMN part_id DROP NOT NULL")
    op.execute(
        """
        ALTER TABLE lots
        ADD CONSTRAINT lots_part_id_fkey
        FOREIGN KEY (part_id) REFERENCES parts(id)
        ON DELETE SET NULL
        NOT VALID
        """
    )
    op.execute("ALTER TABLE lots VALIDATE CONSTRAINT lots_part_id_fkey")


def _set_lots_part_cascade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM lots WHERE part_id IS NULL) THEN
            RAISE EXCEPTION
              'cannot downgrade 0058 while lots rows have NULL part_id';
          END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE lots DROP CONSTRAINT IF EXISTS lots_part_id_fkey")
    op.execute("ALTER TABLE lots ALTER COLUMN part_id SET NOT NULL")
    op.execute(
        """
        ALTER TABLE lots
        ADD CONSTRAINT lots_part_id_fkey
        FOREIGN KEY (part_id) REFERENCES parts(id)
        ON DELETE CASCADE
        NOT VALID
        """
    )
    op.execute("ALTER TABLE lots VALIDATE CONSTRAINT lots_part_id_fkey")


def upgrade() -> None:
    _set_lots_part_set_null()
    op.execute(_STOCK_ENTRIES_UPDATE_GATED_SQL)
    op.execute(_PART_SUBSTITUTES_UPDATE_GATED_SQL)
    op.execute(_PART_META_MEMBERS_UPDATE_GATED_SQL)
    op.execute(_PART_CAD_KEYS_UPDATE_GATED_SQL)


def downgrade() -> None:
    op.execute(_PART_CAD_KEYS_FULL_RECHECK_SQL)
    op.execute(_PART_META_MEMBERS_FULL_RECHECK_SQL)
    op.execute(_PART_SUBSTITUTES_FULL_RECHECK_SQL)
    op.execute(_STOCK_ENTRIES_FULL_RECHECK_SQL)
    _set_lots_part_cascade()
