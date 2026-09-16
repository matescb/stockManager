"""Per-category KiCad Value template: `part_categories.value_template`
and `part_categories.kicad_fields`.

A KiCad symbol's `Value` was `part_eda.value` if a human had typed one
and `parts.name` otherwise — and for the ~40% of the library imported
from a provider, `name` is the provider's marketing description. These
two columns let a category say what its parts' `Value` is made of, from
the canonical specs the part already carries as `custom_fields` rows:

* `value_template` — `"{resistance} {tolerance} {package}"` renders
  `10 kΩ 1% 0603`. `{mpn}` is the sensible default for everything that
  is not a passive. Placeholder syntax and the render rules live in
  `domain/eda/value_template.py`; the shape is validated by
  `domain/categories/schemas.py`, not by a CHECK constraint, because the
  vocabulary of legal keys is application data (`spec_schema.py`) and a
  constraint would need a migration every time it grew.
* `kicad_fields` — the canonical spec keys to emit as hidden symbol
  fields (`Resistance`, `Voltage Rating`, …), so the values are
  greppable in the schematic editor and land in a BOM export.

**Why JSONB and not `ARRAY(String)`.** `footprint_filters` on this same
table is `ARRAY(String(100))`, so the array form is the local
precedent — but that column is passed to KiCad verbatim, whereas this
one is an ordered list of *keys* that a later phase will likely want to
carry per-key display options (a unit override, a visibility flag).
JSONB takes that without a migration; a text array would need one. The
cost is that the list is validated in Pydantic rather than by the column
type, which is where `value_template`'s validation has to live anyway.

Both columns are nullable with no default and no backfill: a category
with neither behaves exactly as it did before this migration, which is
what makes the deploy a no-op until somebody fills one in. Seeding the
passive categories with real templates is deliberately a separate job,
not this migration — it is workspace data, and a data write hidden in a
schema migration is not reviewable as either.

**Locks.** Two `ADD COLUMN` statements, both nullable with no default,
so neither rewrites the table: Postgres 11+ records the added attribute
in the catalog and existing rows are unchanged. ACCESS EXCLUSIVE is held
for the catalog update only, on a table holding one workspace-sized
listing of categories.

Reversibility: total. `downgrade()` drops both columns and touches
nothing else. Dropping them loses any templates a workspace had typed,
which is the ordinary cost of reverting a feature that stores data.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "part_categories",
        sa.Column("value_template", sa.String(200), nullable=True),
    )
    op.add_column(
        "part_categories",
        sa.Column("kicad_fields", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("part_categories", "kicad_fields")
    op.drop_column("part_categories", "value_template")
