"""Add TrustedParts sourcing response cache.

Revision ID: 0038
Revises: 0037
Create Date: 2026-05-08
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sourcing_cache",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("query_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("query_json", JSONB, nullable=False),
        sa.Column("response_json", JSONB, nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "expires_at <= fetched_at + interval '7 days'",
            name="sourcing_cache_max_7_day_ttl",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_sourcing_cache_ws_qhash",
        "sourcing_cache",
        ["workspace_id", "query_hash"],
        unique=True,
    )
    op.create_index(
        "ix_sourcing_cache_expires_at",
        "sourcing_cache",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_sourcing_cache_expires_at", table_name="sourcing_cache")
    op.drop_index("uq_sourcing_cache_ws_qhash", table_name="sourcing_cache")
    op.drop_table("sourcing_cache")
