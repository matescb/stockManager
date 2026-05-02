"""Add pending_users table for email-verified signup (SEC2-014).

Revision ID: 0029
Revises: 0028
Create Date: 2026-05-02

Email-verification flow:
- New table `pending_users`: holds a signup record between the initial
  POST /auth/signup and the email-link POST /auth/verify.
- On verify, the row is promoted atomically to User + Workspace +
  WorkspaceMember; `verified_at` is set before the transaction commits.
- Rows older than 24 h are reaped (no explicit DB-side TTL — the
  reap is application-driven on the verify endpoint).

NOTE: this table has no workspace_id FK — signup precedes workspace
creation.  This is intentional; see CLAUDE.md "workspace isolation" note.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=500), nullable=False),
        sa.Column("workspace_name", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("verification_token_hmac", sa.String(length=64), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pending_users_email",
        "pending_users",
        ["email"],
    )
    op.create_index(
        "ix_pending_users_created_at",
        "pending_users",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pending_users_created_at", table_name="pending_users")
    op.drop_index("ix_pending_users_email", table_name="pending_users")
    op.drop_table("pending_users")
