"""Drop redundant two-column polymorphic indexes.

Revision ID: 0052
Revises: 0051
Create Date: 2026-05-14

The cleanup queries filter by workspace_id, object_type, and object_id, which
is covered by the existing three-column polymorphic object indexes. The
two-column workspace_id/object_id indexes from 0033 add write overhead without
serving a remaining query shape.
"""

from __future__ import annotations

from alembic import op


revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_attachments_ws_objid_only")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_cf_ws_objid_only")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_tag_link_ws_objid_only")


def downgrade() -> None:
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
