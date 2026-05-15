"""Inline workspace-isolation trigger SQLSTATE rewrite.

Revision ID: 0064
Revises: 0063
Create Date: 2026-05-15

AUD-093 / issue #750.

This follows 0063 from main, preserving a single Alembic head.

Recreate the update-gated trigger definitions from 0058 inline while changing
their SQLSTATE, so the migration does not depend on database-rendered function
text.
"""

from __future__ import annotations

from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def _superseded_0060_downgrade_marker(sqlstate: str) -> str:
    if sqlstate != "23514":
        return ""
    return """
  -- 0060's superseded downgrade still looks for this token when rolling
  -- back past it. Keep it as a no-op so full-chain downgrades remain valid.
  PERFORM $superseded_0060$ERRCODE = 'WS001'$superseded_0060$;
"""


def _stock_entries_workspace_fks_sql(sqlstate: str) -> str:
    return f"""
CREATE OR REPLACE FUNCTION check_stock_entries_workspace_fks()
RETURNS trigger AS $$
BEGIN
{_superseded_0060_downgrade_marker(sqlstate)}
  IF TG_OP = 'INSERT' THEN
    IF NEW.part_id IS NULL THEN
      RAISE EXCEPTION 'stock_entries.part_id is required on insert'
        USING ERRCODE = '{sqlstate}';
    END IF;

    PERFORM 1 FROM parts
     WHERE id = NEW.part_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stock_entries.part_id (%) not in workspace (%)',
        NEW.part_id, NEW.workspace_id
        USING ERRCODE = '{sqlstate}';
    END IF;

    IF NEW.lot_id IS NOT NULL THEN
      PERFORM 1 FROM lots
       WHERE id = NEW.lot_id
         AND workspace_id = NEW.workspace_id;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'stock_entries.lot_id (%) not in workspace (%)',
          NEW.lot_id, NEW.workspace_id
          USING ERRCODE = '{sqlstate}';
      END IF;
    END IF;

    IF NEW.storage_location_id IS NOT NULL THEN
      PERFORM 1 FROM storage_locations
       WHERE id = NEW.storage_location_id
         AND workspace_id = NEW.workspace_id;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'stock_entries.storage_location_id (%) not in workspace (%)',
          NEW.storage_location_id, NEW.workspace_id
          USING ERRCODE = '{sqlstate}';
      END IF;
    END IF;

    IF NEW.related_entry_id IS NOT NULL THEN
      PERFORM 1 FROM stock_entries
       WHERE id = NEW.related_entry_id
         AND workspace_id = NEW.workspace_id;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'stock_entries.related_entry_id (%) not in workspace (%)',
          NEW.related_entry_id, NEW.workspace_id
          USING ERRCODE = '{sqlstate}';
      END IF;
    END IF;

    IF NEW.order_id IS NOT NULL THEN
      PERFORM 1 FROM orders
       WHERE id = NEW.order_id
         AND workspace_id = NEW.workspace_id;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'stock_entries.order_id (%) not in workspace (%)',
          NEW.order_id, NEW.workspace_id
          USING ERRCODE = '{sqlstate}';
      END IF;
    END IF;

    IF NEW.order_entry_id IS NOT NULL THEN
      PERFORM 1 FROM order_entries
       WHERE id = NEW.order_entry_id
         AND workspace_id = NEW.workspace_id;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'stock_entries.order_entry_id (%) not in workspace (%)',
          NEW.order_entry_id, NEW.workspace_id
          USING ERRCODE = '{sqlstate}';
      END IF;
    END IF;

    IF NEW.project_id IS NOT NULL THEN
      PERFORM 1 FROM projects
       WHERE id = NEW.project_id
         AND workspace_id = NEW.workspace_id;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'stock_entries.project_id (%) not in workspace (%)',
          NEW.project_id, NEW.workspace_id
          USING ERRCODE = '{sqlstate}';
      END IF;
    END IF;

    IF NEW.build_id IS NOT NULL THEN
      PERFORM 1 FROM builds
       WHERE id = NEW.build_id
         AND workspace_id = NEW.workspace_id;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'stock_entries.build_id (%) not in workspace (%)',
          NEW.build_id, NEW.workspace_id
          USING ERRCODE = '{sqlstate}';
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
        USING ERRCODE = '{sqlstate}';
    END IF;
  END IF;

  IF NEW.lot_id IS DISTINCT FROM OLD.lot_id AND NEW.lot_id IS NOT NULL THEN
    PERFORM 1 FROM lots
     WHERE id = NEW.lot_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stock_entries.lot_id (%) not in workspace (%)',
        NEW.lot_id, NEW.workspace_id
        USING ERRCODE = '{sqlstate}';
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
        USING ERRCODE = '{sqlstate}';
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
        USING ERRCODE = '{sqlstate}';
    END IF;
  END IF;

  IF NEW.order_id IS DISTINCT FROM OLD.order_id AND NEW.order_id IS NOT NULL THEN
    PERFORM 1 FROM orders
     WHERE id = NEW.order_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stock_entries.order_id (%) not in workspace (%)',
        NEW.order_id, NEW.workspace_id
        USING ERRCODE = '{sqlstate}';
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
        USING ERRCODE = '{sqlstate}';
    END IF;
  END IF;

  IF NEW.project_id IS DISTINCT FROM OLD.project_id AND NEW.project_id IS NOT NULL THEN
    PERFORM 1 FROM projects
     WHERE id = NEW.project_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stock_entries.project_id (%) not in workspace (%)',
        NEW.project_id, NEW.workspace_id
        USING ERRCODE = '{sqlstate}';
    END IF;
  END IF;

  IF NEW.build_id IS DISTINCT FROM OLD.build_id AND NEW.build_id IS NOT NULL THEN
    PERFORM 1 FROM builds
     WHERE id = NEW.build_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'stock_entries.build_id (%) not in workspace (%)',
        NEW.build_id, NEW.workspace_id
        USING ERRCODE = '{sqlstate}';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def _part_substitutes_workspace_fks_sql(sqlstate: str) -> str:
    return f"""
CREATE OR REPLACE FUNCTION check_part_substitutes_workspace_fks()
RETURNS trigger AS $$
BEGIN
{_superseded_0060_downgrade_marker(sqlstate)}
  IF TG_OP = 'INSERT' THEN
    PERFORM 1 FROM parts
     WHERE id = NEW.part_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'part_substitutes.part_id (%) not in workspace (%)',
        NEW.part_id, NEW.workspace_id
        USING ERRCODE = '{sqlstate}';
    END IF;

    PERFORM 1 FROM parts
     WHERE id = NEW.substitute_part_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'part_substitutes.substitute_part_id (%) not in workspace (%)',
        NEW.substitute_part_id, NEW.workspace_id
        USING ERRCODE = '{sqlstate}';
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
        USING ERRCODE = '{sqlstate}';
    END IF;
  END IF;

  IF NEW.substitute_part_id IS DISTINCT FROM OLD.substitute_part_id THEN
    PERFORM 1 FROM parts
     WHERE id = NEW.substitute_part_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'part_substitutes.substitute_part_id (%) not in workspace (%)',
        NEW.substitute_part_id, NEW.workspace_id
        USING ERRCODE = '{sqlstate}';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def _part_meta_members_workspace_fks_sql(sqlstate: str) -> str:
    return f"""
CREATE OR REPLACE FUNCTION check_part_meta_members_workspace_fks()
RETURNS trigger AS $$
BEGIN
{_superseded_0060_downgrade_marker(sqlstate)}
  IF TG_OP = 'INSERT' THEN
    PERFORM 1 FROM parts
     WHERE id = NEW.meta_part_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'part_meta_members.meta_part_id (%) not in workspace (%)',
        NEW.meta_part_id, NEW.workspace_id
        USING ERRCODE = '{sqlstate}';
    END IF;

    PERFORM 1 FROM parts
     WHERE id = NEW.part_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'part_meta_members.part_id (%) not in workspace (%)',
        NEW.part_id, NEW.workspace_id
        USING ERRCODE = '{sqlstate}';
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
        USING ERRCODE = '{sqlstate}';
    END IF;
  END IF;

  IF NEW.part_id IS DISTINCT FROM OLD.part_id THEN
    PERFORM 1 FROM parts
     WHERE id = NEW.part_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'part_meta_members.part_id (%) not in workspace (%)',
        NEW.part_id, NEW.workspace_id
        USING ERRCODE = '{sqlstate}';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def _part_cad_keys_workspace_sql(sqlstate: str) -> str:
    return f"""
CREATE OR REPLACE FUNCTION check_part_cad_keys_workspace()
RETURNS trigger AS $$
BEGIN
{_superseded_0060_downgrade_marker(sqlstate)}
  IF TG_OP = 'INSERT' THEN
    PERFORM 1 FROM parts
     WHERE id = NEW.part_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'part_cad_keys.part_id (%) not in workspace (%)',
        NEW.part_id, NEW.workspace_id
        USING ERRCODE = '{sqlstate}';
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
        USING ERRCODE = '{sqlstate}';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def _rewrite_workspace_trigger_sqlstate(sqlstate: str) -> None:
    op.execute(_stock_entries_workspace_fks_sql(sqlstate))
    op.execute(_part_substitutes_workspace_fks_sql(sqlstate))
    op.execute(_part_meta_members_workspace_fks_sql(sqlstate))
    op.execute(_part_cad_keys_workspace_sql(sqlstate))


def upgrade() -> None:
    _rewrite_workspace_trigger_sqlstate("WS001")


def downgrade() -> None:
    _rewrite_workspace_trigger_sqlstate("23514")
