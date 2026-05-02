"""Enable pg_trgm and add GIN trigram indexes on search columns.

Revision ID: 0024
Revises: 0023
Create Date: 2026-05-02

BE2-018 / issue #63.

GET /api/search?q=... runs five ILIKE '%q%' table-scans. Trigram GIN
indexes make these operator-indexed seeks instead of seqscans.

Columns indexed:
  parts(name, mpn, manufacturer)
  storage_locations(name)
  projects(name)
  lots(name, serial_number)
  orders(name, supplier)

We do NOT include workspace_id in the index predicate — workspace
isolation is enforced in application code (CLAUDE.md invariant), and
GIN doesn't support partial indexes in the same way btree does; the
trigram scan is fast enough because most queries are already filtered
by the planner's workspace_id btree first when selectivity is high.

Plain CREATE INDEX (not CONCURRENT) — alembic runs inside a transaction.
IF NOT EXISTS makes reruns safe on installations that already have the
index from a failed partial run.
"""
from __future__ import annotations

from alembic import op


revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable the trigram extension once for the whole database.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # parts
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_parts_name_trgm "
        "ON parts USING GIN (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_parts_mpn_trgm "
        "ON parts USING GIN (mpn gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_parts_manufacturer_trgm "
        "ON parts USING GIN (manufacturer gin_trgm_ops)"
    )

    # storage_locations
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_storage_locations_name_trgm "
        "ON storage_locations USING GIN (name gin_trgm_ops)"
    )

    # projects
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_projects_name_trgm "
        "ON projects USING GIN (name gin_trgm_ops)"
    )

    # lots
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_lots_name_trgm "
        "ON lots USING GIN (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_lots_serial_number_trgm "
        "ON lots USING GIN (serial_number gin_trgm_ops)"
    )

    # orders
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orders_name_trgm "
        "ON orders USING GIN (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orders_supplier_trgm "
        "ON orders USING GIN (supplier gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_orders_supplier_trgm")
    op.execute("DROP INDEX IF EXISTS ix_orders_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_lots_serial_number_trgm")
    op.execute("DROP INDEX IF EXISTS ix_lots_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_projects_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_storage_locations_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_parts_manufacturer_trgm")
    op.execute("DROP INDEX IF EXISTS ix_parts_mpn_trgm")
    op.execute("DROP INDEX IF EXISTS ix_parts_name_trgm")
    # We do NOT drop the pg_trgm extension — other code may rely on it
    # and extensions are DB-wide. Leave it installed.
