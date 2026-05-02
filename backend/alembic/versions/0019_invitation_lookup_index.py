"""Partial composite index on workspace_invitations for the canonical
"pending invitation for this email in this workspace?" lookup.

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-02

DB-014 / issue #105. Today the table has only single-column btree
indexes on `workspace_id` and `email`; Postgres bitmap-merges them for
the hot lookup at `app/api/routes/invitations.py::create_invitation`,
which filters
  WHERE workspace_id = … AND lower(email) = … AND status = 'pending'
The volume is tiny so this is mild — the issue body itself flags the
fix as optional. We ship a partial composite anyway because the
storage cost is negligible (status='pending' is a small subset) and
the planner can collapse the lookup to a single index scan instead of
a bitmap-merge.

NOTE on chain: PR #137 (#98 / DB-007) was expected to land first and
reserve 0019, but has not yet, so this migration takes 0019 as the next
free integer in the chain. If #137 lands first after a rebase, bump
filename + revision to the next free integer and update down_revision
accordingly. **Don't edit a migration once it's on main** (CLAUDE.md
invariant).

Email normalisation: the matching application code in
`invitations.py` lowercases payload.email before both the
duplicate-check query and the row insert, so the index's
`lower(email)` expression matches what the planner sees.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


_INDEX_NAME = "ix_invitations_pending_lookup"


def upgrade() -> None:
    # Backfill: lowercase any pre-existing rows so the partial index
    # matches uniformly. Practically a no-op (signup/admin invite UI
    # already passes lowercased emails through Pydantic's EmailStr),
    # but cheap insurance.
    op.execute(
        "UPDATE workspace_invitations "
        "SET email = lower(email) "
        "WHERE email <> lower(email)"
    )

    # Partial composite. `lower(email)` must be wrapped in `sa.text`
    # because op.create_index doesn't accept SQL expressions in the
    # column list directly; passing the raw column name would index
    # the original (possibly mixed-case) value.
    op.execute(
        f"CREATE INDEX {_INDEX_NAME} "
        f"ON workspace_invitations (workspace_id, lower(email)) "
        f"WHERE status = 'pending'"
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="workspace_invitations")
