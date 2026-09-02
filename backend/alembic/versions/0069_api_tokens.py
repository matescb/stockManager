"""Add api_tokens (personal access tokens).

Revision ID: 0069
Revises: 0068
Create Date: 2026-09-02
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        # HMAC-SHA256 hex digest of the secret half of the plaintext,
        # keyed on SESSION_SECRET. The plaintext is never stored.
        sa.Column("token_hmac", sa.String(length=64), nullable=False),
        sa.Column(
            "read_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_ip", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        # CASCADE, not SET NULL: a token whose owner is gone has no
        # membership to resolve a role from, so it must not survive.
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_tokens_workspace_id", "api_tokens", ["workspace_id"])
    op.create_index("ix_api_tokens_archived_at", "api_tokens", ["archived_at"])
    op.create_index("ix_api_tokens_user_id", "api_tokens", ["user_id"])
    # Drives both listing paths (own tokens, and the admin "every token
    # in the workspace" view). Authentication itself is a primary-key
    # lookup — the token id travels in the plaintext — so no index on
    # token_hmac is needed, and deliberately none exists: nothing should
    # ever scan that column.
    op.create_index(
        "ix_api_tokens_ws_revoked", "api_tokens", ["workspace_id", "revoked_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_api_tokens_ws_revoked", table_name="api_tokens")
    op.drop_index("ix_api_tokens_user_id", table_name="api_tokens")
    op.drop_index("ix_api_tokens_archived_at", table_name="api_tokens")
    op.drop_index("ix_api_tokens_workspace_id", table_name="api_tokens")
    op.drop_table("api_tokens")
