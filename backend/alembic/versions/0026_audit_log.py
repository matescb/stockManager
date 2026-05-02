"""Add audit_log table for per-action trail (BE2-024).

Revision ID: 0026
Revises: 0023
Create Date: 2026-05-02

Tracks state-changing operations (bulk_delete, archive/restore,
credential rotation, invitation lifecycle, member role/status changes)
with workspace_id + user_id context and a UUID[] of affected objects.

Chain note: Renumbered from 0024 to 0026; down_revision updated from
"0021" to "0023" to chain off main's current head after migrations 0022
(invitation_token_hmac) and 0023 (invitation_pending_unique) landed.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0026"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("target_type", sa.Text, nullable=True),
        sa.Column("target_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=True),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("request_id", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Primary lookup: workspace logs in reverse-chron order.
    op.create_index(
        "ix_audit_log_workspace_created",
        "audit_log",
        ["workspace_id", sa.text("created_at DESC")],
        postgresql_ops={"created_at": "DESC"},
    )

    # GIN index for "find all audit rows that mention this UUID".
    op.execute(
        "CREATE INDEX ix_audit_log_target_ids_gin "
        "ON audit_log USING gin(target_ids)"
    )


def downgrade() -> None:
    op.drop_index("ix_audit_log_target_ids_gin", table_name="audit_log")
    op.drop_index("ix_audit_log_workspace_created", table_name="audit_log")
    op.drop_table("audit_log")
