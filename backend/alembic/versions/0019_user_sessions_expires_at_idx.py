"""Index user_sessions.expires_at + give the periodic cleanup task a
seekable column.

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-02

DB-007 / issue #98. Closes the unbounded-growth gap on `user_sessions`:
prior to this migration the only index was `ix_user_sessions_user_id`,
so any "delete expired" sweep was a full table scan. The plan was to
keep these rows around forever — sessions were only deleted on explicit
logout — so a long-running prod accumulates every expired row.

Pairs with the lifespan-hook purge in `app/main.py` that runs
`DELETE FROM user_sessions WHERE expires_at < now()` once an hour. The
DELETE planner picks this index because every row has a non-null
`expires_at`; no partial predicate is needed.

NOTE on chain: this migration is numbered 0019 because main had 0018
at the time of authoring. Several open PRs in adjacent batches may
also want 0019 — if any of them lands first, rebase this onto the new
head and bump both the filename and `revision = ...` to match. Don't
edit a migration once it's on `main` (CLAUDE.md invariant).
"""
from __future__ import annotations

from alembic import op


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_user_sessions_expires_at",
        "user_sessions",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_sessions_expires_at",
        table_name="user_sessions",
    )
