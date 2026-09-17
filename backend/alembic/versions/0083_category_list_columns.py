"""Per-category parts-list spec columns: `part_categories.list_columns`
and `part_categories.list_sort`.

The parts list has eighteen columns and none of them is a spec. A user
looking at resistors wants resistance, tolerance, power and package;
looking at MOSFETs they want Vds, Rds(on) and Id. That choice belongs to
the category, not to the browser: `DataTable`'s hidden-column map is
`localStorage` (`dataTableStorageKey`), so it is per-viewer, per-device
and invisible to everybody else. A library is curated once and read by
the whole workspace, so the column set is stored on the row.

* `list_columns` — an ordered list of canonical spec keys to show as
  columns, e.g. `["resistance", "tolerance", "power"]`. Capped at 12 by
  `domain/categories/schemas.py`; every key is validated against the
  category's effective spec schema
  (`domain/parts/services/spec_columns.py`), so
  a key that is not canonical for this category is a 422 rather than a
  permanently blank column.
* `list_sort` — `{"key": "<spec key>", "dir": "asc"|"desc"}`, the
  default sort the list applies when the request names none.

**Why JSONB and not `ARRAY(String)` / two scalar columns.** Same
argument `value_template` / `kicad_fields` made in 0082, which is the
local precedent on this very table: these are ordered lists of *keys*
that a later phase will want to carry per-key display options (a width,
a unit override, a numeric-format flag), and `list_sort` is a small
record. JSONB takes both without a migration. The cost is that the
shape is validated in Pydantic rather than by the column type — which
is where the key-vocabulary validation has to live anyway, because the
vocabulary is application data (`spec_schema_tables.py`) and a CHECK
constraint would need a migration every time it grew.

Both columns are nullable with no default and no backfill: a category
with neither behaves exactly as it did before this migration, which is
what makes the deploy a no-op until somebody picks columns. NULL
`list_columns` means "inherit" — the spec-schema endpoint walks
`parent_id` for the nearest ancestor that has one, the same way
`kicad_library.py` walks it for `value_template` — and an empty list is
an explicit "no spec columns here" that stops the walk.

**Locks.** Two `ADD COLUMN` statements, both nullable with no default,
so neither rewrites the table: Postgres 11+ records the added attribute
in the catalog and existing rows are unchanged. ACCESS EXCLUSIVE is
held for the catalog update only, on a table holding one
workspace-sized listing of categories.

Reversibility: total. `downgrade()` drops both columns and touches
nothing else. Dropping them loses any column choices a workspace had
made, which is the ordinary cost of reverting a feature that stores
data.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "part_categories",
        sa.Column("list_columns", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "part_categories",
        sa.Column("list_sort", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("part_categories", "list_sort")
    op.drop_column("part_categories", "list_columns")
