"""Backfill `parts.part_type` for parts that are linked but say `local`.

Revision ID: 0080
Revises: 0079
Create Date: 2026-09-15

`part_type` was only ever written at creation. `linked_provider` moved on
without it — the refresh route sets it, the unlink PATCH clears it, and
neither touched the type column. Measured on prod before this landed:

    part_type='local' AND linked_provider IS NOT NULL   160
    part_type='linked' AND linked_provider IS NOT NULL  121
    part_type='local' AND linked_provider IS NULL        43

The first bucket is the bug. Those 160 rows are provider-backed parts
that the UI pill renders as manual ones, because the pill reads the raw
column. `domain/parts/part_type.py` stops the drift from here on; this
migration fixes the rows that already drifted.

Scope is deliberately narrow — only `local` → `linked`, only where a
link exists:

* `meta` and `sub_assembly` are user-declared roles, not derived state,
  so a link never rewrites them and neither does this.
* `linked` with no `linked_provider` is NOT corrected here. It is
  cosmetic rather than misleading (the part really did come from a
  provider once), the next unlink fixes it through the app, and rewriting
  it would touch rows no measurement covered.

`linked_provider` is nullable TEXT with no NOT-EMPTY constraint, so the
predicate treats blank the way `part_type.py::sync_part_type_with_link`
does — as unlinked. Nothing writes `''` today; having the two disagree
would mean a row this migration promoted got demoted again by the next
unlink.

No schema change: the column is `String(20)`, already NOT NULL with a
`local` default, and there is no CHECK constraint on the vocabulary
(`docs/domain/parts.md`). One UPDATE, no lock concern — `parts` is a few
hundred rows per workspace.
"""
from __future__ import annotations

from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE parts
           SET part_type = 'linked'
         WHERE part_type = 'local'
           AND coalesce(btrim(linked_provider), '') <> ''
        """
    )


def downgrade() -> None:
    """Intentionally a no-op.

    The rows this migration rewrote held `local` because nothing
    maintained the column, not because anyone chose it — so there is no
    prior value worth restoring and no record of which rows were touched
    (an unconditional `linked` → `local` would also demote the 121 parts
    that were already correct). Downgrading past 0080 leaves the data
    corrected; only the code that keeps it corrected goes away.
    """
