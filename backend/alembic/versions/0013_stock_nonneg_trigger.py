"""stock_entries non-negative balance trigger

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-02

Defense-in-depth for BE CRIT-1 (stock TOCTOU). The append-only ledger
design's whole rationale is that current_stock = SUM(quantity_delta)
can never go negative. Two concurrent operators consuming from the
same lot can both pass the per-request availability check before
either writes (the read-then-write window is unguarded). The service
layer now takes a `pg_advisory_xact_lock` on (workspace, part) which
serialises writes; this trigger is the database-side fall-back.

Behaviour:
- Fires AFTER INSERT FOR EACH ROW on stock_entries.
- Re-aggregates SUM(quantity_delta) for the (workspace_id, part_id,
  lot_id, storage_location_id, status) tuple including the new row.
- If the sum is negative, raises and rolls back the transaction.

Caveats:
- Trigger uses `IS NOT DISTINCT FROM` so NULL lot_id / storage_location_id
  match each other (per existing aggregation semantics in
  app.domain.stock.service.current_quantity).
- Filters by `status` so reservations and on_hand are tracked
  independently (a positive on_hand entry does not mask a negative
  reserved sum, and vice versa).
- Does NOT validate existing rows at upgrade time. If prod already
  has rows summing negative for any tuple (e.g. due to a past CRIT-1
  incident), this migration upgrades cleanly but the FIRST insert to
  that tuple after deploy will fail with the trigger error. Run the
  pre-deploy verification query in the PR description to catch this
  before it surprises someone.
"""
from alembic import op


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION check_stock_nonneg() RETURNS TRIGGER AS $$
DECLARE
    total NUMERIC;
BEGIN
    SELECT COALESCE(SUM(quantity_delta), 0)
      INTO total
      FROM stock_entries
     WHERE workspace_id = NEW.workspace_id
       AND part_id = NEW.part_id
       AND status = NEW.status
       AND lot_id IS NOT DISTINCT FROM NEW.lot_id
       AND storage_location_id IS NOT DISTINCT FROM NEW.storage_location_id;

    IF total < 0 THEN
        RAISE EXCEPTION
            'cumulative stock balance would be negative (%); '
            'workspace=% part=% lot=% storage=% status=%',
            total, NEW.workspace_id, NEW.part_id,
            NEW.lot_id, NEW.storage_location_id, NEW.status
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(_FUNCTION_SQL)
    op.execute(
        """
        CREATE TRIGGER ck_stock_nonneg
        AFTER INSERT ON stock_entries
        FOR EACH ROW
        EXECUTE FUNCTION check_stock_nonneg();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ck_stock_nonneg ON stock_entries;")
    op.execute("DROP FUNCTION IF EXISTS check_stock_nonneg();")
