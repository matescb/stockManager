"""Add (workspace_id, object_id) indexes on polymorphic tables for orphan-cleanup queries.

Revision ID: 0033
Revises: 0032
Create Date: 2026-05-02

Addresses DB-006: attachments, custom_fields, tag_links have no FK on
object_id. When a parent row is hard-deleted these tables accumulate orphan
rows. The new indexes make the per-(workspace, object_id) orphan-cleanup
query fast enough to run as part of an on-delete hook or a maintenance
script without a full-table scan.

Production note: these are existing populated tables that are written on
every part edit / tag change / attachment upload. Plain `CREATE INDEX`
takes a write-blocking ShareLock for the duration of the build, which
would stall live traffic during the auto-deploy. Follow the precedent
set by ``0020_partial_bag_signature_index.py``: build the indexes with
``CREATE INDEX CONCURRENTLY`` inside an ``autocommit_block`` so writes
are not blocked. Concurrent DDL cannot run inside a transaction, hence
the autocommit_block wrapper.

See docs/ARCHITECTURE.md — Polymorphic tables contract.
"""
from __future__ import annotations

from alembic import op


revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CONCURRENTLY requires running outside any transaction. alembic's
    # autocommit_block escapes the env.py transaction wrapper for the
    # statements inside it. IF NOT EXISTS lets the migration be safely
    # re-applied if a previous attempt was interrupted mid-build (which
    # can leave an INVALID index behind).
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_attachments_ws_objid_only "
            "ON attachments (workspace_id, object_id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_cf_ws_objid_only "
            "ON custom_fields (workspace_id, object_id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_tag_link_ws_objid_only "
            "ON tag_links (workspace_id, object_id)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_tag_link_ws_objid_only"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_cf_ws_objid_only"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_attachments_ws_objid_only"
        )
