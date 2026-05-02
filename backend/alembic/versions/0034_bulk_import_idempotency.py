"""Add bulk_import_idempotency table (BE2-003).

Revision ID: 0034
Revises: 0033
Create Date: 2026-05-02

Chain: 0030_audit_log -> 0031_search_pg_trgm_indexes ->
0032_integer_quantities -> 0033_polymorphic_orphan_indexes ->
0034_bulk_import_idempotency.

Adds an idempotency cache for POST /api/parts/bulk-import-from-scan.
A (workspace_id, key) composite PK enforces workspace isolation; a
supporting index on (workspace_id, created_at) serves the 24-hour TTL
sweep that keeps the table bounded without a cron job.

The key is either a client-supplied UUID4 (re-sent unchanged on retry)
or a server-derived SHA-256 content hash of the full row payload.
result_json stores the full API envelope verbatim so a cache hit can be
returned without re-running any provider calls.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bulk_import_idempotency",
        sa.Column("workspace_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("result_json", JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "key"),
    )
    op.create_index(
        "ix_bulk_import_idempotency_ws_created",
        "bulk_import_idempotency",
        ["workspace_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bulk_import_idempotency_ws_created",
        table_name="bulk_import_idempotency",
    )
    op.drop_table("bulk_import_idempotency")
