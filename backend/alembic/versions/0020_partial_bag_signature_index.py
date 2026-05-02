"""Partial bag_signature index (DB-008).

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-02

The original index from `0012` (`ix_stock_ws_bag_signature` on
`(workspace_id, bag_signature)`) had no predicate. Almost every
`stock_entries` row has `bag_signature IS NULL` (only scan-import
rows ever set it), so the index was bloated and paid an insert-time
cost on every ledger write for entries that will never be looked up
by the bag-rescan flow.

This migration drops the existing non-partial index and recreates it
as a partial index with `WHERE bag_signature IS NOT NULL`. Same name,
same column ordering — only the predicate changes.

Production note: `stock_entries` is the hottest write table; both
`DROP INDEX` and `CREATE INDEX` run with `CONCURRENTLY` so writes
aren't blocked. Concurrent index DDL cannot run inside a transaction,
so we use alembic's `autocommit_block()` to escape the wrapping
transaction for these statements only.
"""
from alembic import op


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CONCURRENTLY requires running outside any transaction. alembic's
    # autocommit_block escapes the env.py transaction wrapper for the
    # statements inside it.
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_stock_ws_bag_signature"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY ix_stock_ws_bag_signature "
            "ON stock_entries (workspace_id, bag_signature) "
            "WHERE bag_signature IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_stock_ws_bag_signature"
        )
        # Recreate the original (non-partial) index so a downgrade
        # leaves the schema in the same shape as it was after `0012`.
        op.execute(
            "CREATE INDEX CONCURRENTLY ix_stock_ws_bag_signature "
            "ON stock_entries (workspace_id, bag_signature)"
        )
