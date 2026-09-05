"""Units of measure, step 3: unit stamping backstops.

Revision ID: 0077
Revises: 0076
Create Date: 2026-09-05

Migration 0074 added `parts.unit_of_measure` and the per-row
`stock_entries.unit`, both `NOT NULL DEFAULT 'pcs'`, and deliberately
deferred the "write the part's unit onto the row" half to this step. That
half is now in `domain/stock/service.py::unit_for_part`, called from every
one of the eleven `StockEntry(...)` constructions the CI guard
`backend/scripts/check_stockentry_constructors.py` allows. This migration
adds the three database triggers that keep the stamp meaningful.

**These are backstops, not the primary control** — the same posture
`CLAUDE.md` records for workspace isolation ("enforced in code, not the
DB", with `parts_default_storage_workspace_check` from 0036 and
`check_stock_entries_workspace_fks` from 0050 as the DB-side defence). The
service layer stamps; a trigger only ever fires for raw SQL that bypassed
it. They are modelled on 0050's shape — a plpgsql `BEFORE` row trigger
raising with an explicit `USING ERRCODE`.

**On the SQLSTATE.** These raise plain `23514` (check_violation), *not*
the `WS001` that migration 0064 gave the workspace-isolation triggers.
That is deliberate: `WS001` is the code
`routes/_stock_integrity.py::raise_integrity_as_409` translates into a
public 409 "workspace isolation violation", and a unit mismatch is not
one. It is also not a condition any API caller can provoke — the service
stamps from the same part the trigger checks against, so reaching one of
these means the service layer was bypassed. That is a bug in the caller,
and `raise_integrity_as_409`'s fall-through (`raise exc`, i.e. a 500 and
a Sentry event) is the right answer for it. `23514` also matches what the
`ck_*` CHECK constraints already raise, so nothing downstream has to
learn a new code.

1. `stock_entries_unit_match_check` (BEFORE INSERT)
   ------------------------------------------------
   A ledger row's `unit` must equal its part's `unit_of_measure`. Mixing
   units inside one part's ledger has to be impossible rather than merely
   discouraged: `current_quantity` is `SUM(quantity_delta)` over the rows a
   filter selects, and summing 5 pcs with 3 m yields a number that means
   nothing. A DB `CHECK` cannot reference another table, hence a trigger.

   Two deliberate silences, both about *not* answering a question that is
   not this trigger's to answer:

   * `NEW.part_id IS NULL` -> pass. The column is nullable by design
     (`ON DELETE SET NULL`, see ADR-0028): a hard-deleted part leaves its
     ledger history behind, and those rows keep the stamp that is now the
     only record of what they measured.
   * the part is not in `NEW.workspace_id` -> pass, and let
     `stock_entries_workspace_fk_check` raise its `WS001`. The lookup is
     scoped by workspace_id precisely so this trigger cannot become a
     cross-workspace existence oracle: an unscoped lookup would let a
     caller distinguish "no such part" from "a part in someone else's
     workspace, measured in metres" by the error text alone, and BEFORE
     ROW triggers fire in **alphabetical name order**, so
     `..._unit_match_check` runs *before* `..._workspace_fk_check` and
     would get to speak first. Pinned by
     `tests/test_stock_unit_stamping.py::
     test_unit_trigger_is_not_a_cross_workspace_existence_oracle`.

2. `stock_entries_unit_immutable_check` (BEFORE UPDATE OF unit)
   ------------------------------------------------------------
   The ledger is append-only, so a row's unit is a historical fact and can
   never change. `UPDATE OF unit` means the trigger is only considered when
   the column appears in the SET list — an UPDATE that does not mention it
   cannot change it, so there is nothing to pay for on the common path.

3. `parts_unit_of_measure_change_check` (BEFORE UPDATE OF unit_of_measure)
   ------------------------------------------------------------------------
   **Decision: a part's unit is frozen the moment it has any ledger row —
   not "unless the net balance is zero".**

   The looser rule is tempting (the uom design sketches it) and it is
   wrong here. Zeroing a part's stock does not remove its ledger rows;
   nothing does, that is the point of an append-only ledger. Allowing the
   change at net-zero would leave one part's history holding `pcs` rows
   and `m` rows, and every roll-up built on `current_quantity` — the
   per-bucket sums, `stock_summary_for_part`, `history_for_part`, the
   reports — would go on adding them together. "No ledger rows at all" is
   the only rule under which a part's ledger is single-unit *by
   construction*, which is exactly the invariant the sums need.

   Refusing outright would also have been defensible, but it leaves a
   freshly created part — the overwhelmingly common case for wanting to
   set a unit — permanently stuck on `pcs`, which would make the whole
   feature unusable. The supported path for a part that already has
   history is the one that leaves a correct audit trail: zero the stock
   out, hard-delete the ledger, or create the part anew.

   The existence probe is scoped by workspace_id as well as part_id: it
   matches `ix_stock_ws_part_status`'s leading columns, and parts and their
   ledger rows always share a workspace (0050 enforces it), so scoping
   cannot hide a row.

   No application path can change `parts.unit_of_measure` today —
   `PartIn` / `PartPatch` have no such field, so the generic `setattr`
   loop in `routes/parts_core.py` cannot reach it. The rule lands here
   *before* the route that needs it, so the route cannot be written
   without it.

Reversibility: total. This migration adds three functions and three
triggers and touches no data, so `downgrade()` is a clean drop.
"""
from __future__ import annotations

from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


_UNIT_MATCH_FUNCTION = """
CREATE OR REPLACE FUNCTION check_stock_entry_unit_matches_part()
RETURNS trigger AS $$
DECLARE
  part_unit text;
BEGIN
  -- Ledger rows outlive their part (ON DELETE SET NULL, ADR-0028). There
  -- is nothing left to compare against, and the stamp is the only record
  -- of what the row measured.
  IF NEW.part_id IS NULL THEN
    RETURN NEW;
  END IF;

  SELECT unit_of_measure INTO part_unit
    FROM parts
   WHERE id = NEW.part_id
     AND workspace_id = NEW.workspace_id;

  -- Not found: either no such part, or a part in another workspace.
  -- Either way `stock_entries_workspace_fk_check` owns that error;
  -- answering here would make this trigger a cross-workspace oracle.
  IF NOT FOUND THEN
    RETURN NEW;
  END IF;

  IF NEW.unit IS DISTINCT FROM part_unit THEN
    RAISE EXCEPTION
      'stock_entries.unit (%) does not match parts.unit_of_measure (%) for part (%)',
      NEW.unit, part_unit, NEW.part_id
      USING ERRCODE = '23514';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_UNIT_IMMUTABLE_FUNCTION = """
CREATE OR REPLACE FUNCTION check_stock_entry_unit_immutable()
RETURNS trigger AS $$
BEGIN
  IF NEW.unit IS DISTINCT FROM OLD.unit THEN
    RAISE EXCEPTION
      'stock_entries.unit is immutable (row %: % -> %); the ledger is append-only',
      OLD.id, OLD.unit, NEW.unit
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_PART_UNIT_FUNCTION = """
CREATE OR REPLACE FUNCTION check_part_unit_of_measure_change()
RETURNS trigger AS $$
BEGIN
  IF NEW.unit_of_measure IS NOT DISTINCT FROM OLD.unit_of_measure THEN
    RETURN NEW;
  END IF;

  PERFORM 1 FROM stock_entries
   WHERE workspace_id = NEW.workspace_id
     AND part_id = NEW.id
   LIMIT 1;

  IF FOUND THEN
    RAISE EXCEPTION
      'parts.unit_of_measure (%) cannot change to (%) for part (%): the part has stock ledger rows',
      OLD.unit_of_measure, NEW.unit_of_measure, NEW.id
      USING ERRCODE = '23514',
        HINT = 'The ledger is append-only, so existing rows keep the old unit '
               'and the part''s sums would mix units. Zero the stock out and '
               're-add it, or create a new part.';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(_UNIT_MATCH_FUNCTION)
    op.execute("""
    CREATE TRIGGER stock_entries_unit_match_check
      BEFORE INSERT ON stock_entries
      FOR EACH ROW
      EXECUTE FUNCTION check_stock_entry_unit_matches_part();
    """)

    op.execute(_UNIT_IMMUTABLE_FUNCTION)
    op.execute("""
    CREATE TRIGGER stock_entries_unit_immutable_check
      BEFORE UPDATE OF unit ON stock_entries
      FOR EACH ROW
      EXECUTE FUNCTION check_stock_entry_unit_immutable();
    """)

    op.execute(_PART_UNIT_FUNCTION)
    op.execute("""
    CREATE TRIGGER parts_unit_of_measure_change_check
      BEFORE UPDATE OF unit_of_measure ON parts
      FOR EACH ROW
      EXECUTE FUNCTION check_part_unit_of_measure_change();
    """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS parts_unit_of_measure_change_check ON parts;"
    )
    op.execute("DROP FUNCTION IF EXISTS check_part_unit_of_measure_change();")

    op.execute(
        "DROP TRIGGER IF EXISTS stock_entries_unit_immutable_check ON stock_entries;"
    )
    op.execute("DROP FUNCTION IF EXISTS check_stock_entry_unit_immutable();")

    op.execute(
        "DROP TRIGGER IF EXISTS stock_entries_unit_match_check ON stock_entries;"
    )
    op.execute("DROP FUNCTION IF EXISTS check_stock_entry_unit_matches_part();")
