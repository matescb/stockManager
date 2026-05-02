"""Add user_login_failures table for per-account login lockout (SEC2-014).

Revision ID: 0028
Revises: 0025
Create Date: 2026-05-02

Per-account lockout:
- New table `user_login_failures`: tracks failed login attempts per
  user/email-hash.  The lockout check counts rows within the last
  LOCKOUT_WINDOW_MINUTES; a successful login deletes all rows for that user.
- `user_id` is SET NULL on user deletion so audit rows are retained as
  orphaned tombstones without a dangling FK.
- `email_hash` (SHA-256 of the lowercased email) allows capping failed
  attempts for unknown-email stuffing without leaking user existence or
  storing PII.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0028"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_login_failures",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("email_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("client_ip", sa.String(length=45), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_user_login_failures_user_id",
        "user_login_failures",
        ["user_id"],
    )
    op.create_index(
        "ix_user_login_failures_email_hash",
        "user_login_failures",
        ["email_hash"],
    )
    op.create_index(
        "ix_user_login_failures_occurred_at",
        "user_login_failures",
        ["occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_login_failures_occurred_at", table_name="user_login_failures")
    op.drop_index("ix_user_login_failures_email_hash", table_name="user_login_failures")
    op.drop_index("ix_user_login_failures_user_id", table_name="user_login_failures")
    op.drop_table("user_login_failures")
