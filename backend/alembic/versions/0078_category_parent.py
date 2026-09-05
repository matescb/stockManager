"""Hierarchical part categories: `part_categories.parent_id`.

Revision ID: 0078
Revises: 0077
Create Date: 2026-09-05

`part_categories` was flat (0067). This adds the one column a KiCad-style
library tree needs — a self-referencing adjacency-list parent — and the
workspace guard that keeps it honest.

**Why `ON DELETE SET NULL` and not `CASCADE`.** Every other optional FK in
this schema uses `SET NULL`: `parts.category_id` (0067),
`eda_*.category_id` (0068), `lots.parent_lot_id` (0001), and the
hard-delete policy in ADR-0028 is built on it. `CASCADE` here would mean
one delete of a mid-tree row silently destroying an entire subtree of
categories *and* (via `parts.category_id`'s own `SET NULL`) unfiling every
part underneath it. `SET NULL` instead **promotes the orphans to root** —
nothing is lost, the tree just gets flatter. That is the safe failure mode,
but it is a surprising one, so the service layer applies the same rule on
archive and the UI says so in the confirm dialog.

**Why no closure table / materialized path / recursive CTE.** There is no
recursive query anywhere in this repo, and a category tree is a handful of
rows read once per request. `domain/categories/tree.py` loads
`(id, parent_id)` for the workspace and walks it in Python — cycle
detection, depth capping and descendant expansion all come out of the same
two-column map. Introducing the repo's first recursive CTE to answer a
question a dict lookup answers is not a trade worth making; revisit only if
category counts ever reach a scale where the map itself is the cost.

**The trigger** is defence-in-depth, exactly like
`parts_category_workspace_check` (0067:140-163): the application layer
already validates through `assert_in_workspace`, and this stops raw SQL
from smuggling a foreign-workspace parent into the column. It uses the
`TG_OP` short-circuit from 0076 — validate on every INSERT, but on UPDATE
only when `parent_id` or `workspace_id` actually changed — which is the
ADR-0028 workspace-trigger contract.

`ERRCODE = 'WS001'` is the house SQLSTATE for a workspace-isolation
violation, matching 0067 and 0076. Note that `/api/categories` does **not**
wire `raise_integrity_as_409` (only the stock, builds and build-stages
routers do), so a `WS001` raised here would surface as a 500 and a Sentry
event rather than a 409. That is the right outcome: `tree.py`'s
`validate_parent` runs first on every API path, so reaching this trigger
means something bypassed the service layer — a bug in the caller, not a
condition to hand a client a tidy error for.

Deliberately NOT the trigger's job:

* **Cycles.** A BEFORE ROW trigger sees one row at a time and cannot see
  the rest of a multi-statement reparent, so a cycle check here would be
  both incomplete and a lock-order hazard. Self-parent (`parent_id = id`)
  likewise passes at the DB level. `tree.py::validate_parent` owns both.
* **The parent side of a workspace move.** Re-pointing a *parent's*
  `workspace_id` is not re-validated against its children. That is the
  ADR-0028 "validate changed refs only on UPDATE" contract, shared with
  0067; no code path updates `workspace_id` on an existing category.

**Locks.** All four DDL statements run in one transaction holding ACCESS
EXCLUSIVE on `part_categories`, and that is fine here: the table was
created in 0067, holds one workspace-sized listing of categories, and the
FK's validation scan short-circuits on a column that is 100% NULL because
it was added two statements earlier. `CREATE INDEX CONCURRENTLY` is
deliberately not used — it cannot run inside alembic's transaction, and
the `autocommit_block()` it would need trades this migration's atomicity
for nothing on a table this size.

Reversibility: total. One nullable column, one partial index, one FK, one
trigger + function; `downgrade()` drops all five and touches no other data.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


_PARENT_WORKSPACE_FUNCTION = """
CREATE OR REPLACE FUNCTION check_part_categories_parent_workspace()
RETURNS trigger AS $$
BEGIN
  -- TG_OP short-circuit (0076's shape): on UPDATE, re-validate only when
  -- the reference or its workspace actually moved. An UPDATE that touches
  -- neither pays nothing.
  IF TG_OP = 'INSERT'
     OR NEW.parent_id IS DISTINCT FROM OLD.parent_id
     OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id THEN
    IF NEW.parent_id IS NOT NULL THEN
      PERFORM 1 FROM part_categories
       WHERE id = NEW.parent_id
         AND workspace_id = NEW.workspace_id;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'part_categories.parent_id (%) not in workspace (%)',
          NEW.parent_id, NEW.workspace_id
          USING ERRCODE = 'WS001';
      END IF;
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.add_column(
        "part_categories",
        sa.Column("parent_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_part_categories_parent_id",
        "part_categories",
        "part_categories",
        ["parent_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Partial, matching `ix_parts_category_id` (0067): every existing row
    # is a root, so indexing only real parent links keeps it tiny. The
    # index serves the "children of X" read the tree builder never issues
    # today, but `ON DELETE SET NULL` needs it — without it, deleting a
    # category sequential-scans the table to find referencing rows.
    op.create_index(
        "ix_part_categories_parent_id",
        "part_categories",
        ["parent_id"],
        postgresql_where=sa.text("parent_id IS NOT NULL"),
    )

    op.execute(_PARENT_WORKSPACE_FUNCTION)
    op.execute("""
    CREATE TRIGGER part_categories_parent_workspace_check
      BEFORE INSERT OR UPDATE OF parent_id, workspace_id
      ON part_categories
      FOR EACH ROW
      EXECUTE FUNCTION check_part_categories_parent_workspace();
    """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS part_categories_parent_workspace_check "
        "ON part_categories;"
    )
    op.execute("DROP FUNCTION IF EXISTS check_part_categories_parent_workspace();")
    op.drop_index("ix_part_categories_parent_id", table_name="part_categories")
    op.drop_constraint(
        "fk_part_categories_parent_id", "part_categories", type_="foreignkey"
    )
    op.drop_column("part_categories", "parent_id")
