"""Add password reset request table.

Revision ID: 0061
Revises: 0060
Create Date: 2026-05-15

FEAT-001 / issue #721.

This revision intentionally uses the next safe numeric id. PR #736 (`0059`)
and PR #737 (`0060`) merged while this PR was in progress, so this chains
after `0060` to preserve a single Alembic head.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "password_reset_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("email_hash", sa.String(length=64), nullable=False),
        sa.Column("token_hmac", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_password_reset_requests_user_id",
        "password_reset_requests",
        ["user_id"],
    )
    op.create_index(
        "ix_password_reset_requests_email_hash",
        "password_reset_requests",
        ["email_hash"],
    )
    op.create_index(
        "ix_password_reset_requests_token_hmac",
        "password_reset_requests",
        ["token_hmac"],
        unique=True,
    )
    op.create_index(
        "ix_password_reset_requests_created_at",
        "password_reset_requests",
        ["created_at"],
    )
    op.create_index(
        "ix_password_reset_requests_expires_at",
        "password_reset_requests",
        ["expires_at"],
    )
    op.create_index(
        "ix_password_reset_requests_ip",
        "password_reset_requests",
        ["ip"],
    )


def downgrade() -> None:
    op.drop_index("ix_password_reset_requests_ip", table_name="password_reset_requests")
    op.drop_index("ix_password_reset_requests_expires_at", table_name="password_reset_requests")
    op.drop_index("ix_password_reset_requests_created_at", table_name="password_reset_requests")
    op.drop_index("ix_password_reset_requests_token_hmac", table_name="password_reset_requests")
    op.drop_index("ix_password_reset_requests_email_hash", table_name="password_reset_requests")
    op.drop_index("ix_password_reset_requests_user_id", table_name="password_reset_requests")
    op.drop_table("password_reset_requests")
