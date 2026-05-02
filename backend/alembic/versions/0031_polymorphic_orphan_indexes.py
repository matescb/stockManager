"""Add (workspace_id, object_id) indexes on polymorphic tables for orphan-cleanup queries.

Revision ID: 0031
Revises: 0023
Create Date: 2026-05-02

Addresses DB-006: attachments, custom_fields, tag_links have no FK on
object_id. When a parent row is hard-deleted these tables accumulate orphan
rows. The new indexes make the per-(workspace, object_id) orphan-cleanup
query fast enough to run as part of an on-delete hook or a maintenance
script without a full-table scan.

See docs/ARCHITECTURE.md — Polymorphic tables contract.
"""
from __future__ import annotations

from alembic import op


revision = "0031"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_attachments_ws_objid_only",
        "attachments",
        ["workspace_id", "object_id"],
    )
    op.create_index(
        "ix_cf_ws_objid_only",
        "custom_fields",
        ["workspace_id", "object_id"],
    )
    op.create_index(
        "ix_tag_link_ws_objid_only",
        "tag_links",
        ["workspace_id", "object_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_tag_link_ws_objid_only", table_name="tag_links")
    op.drop_index("ix_cf_ws_objid_only", table_name="custom_fields")
    op.drop_index("ix_attachments_ws_objid_only", table_name="attachments")
