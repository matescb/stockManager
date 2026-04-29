"""workspace parts provider

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-29
"""
from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # parts_provider is NOT NULL on a possibly-non-empty workspaces table,
    # so use the server_default → drop pattern (mirrors 0004_part_serialized).
    op.add_column(
        "workspaces",
        sa.Column(
            "parts_provider",
            sa.String(length=40),
            nullable=False,
            server_default="none",
        ),
    )
    op.alter_column("workspaces", "parts_provider", server_default=None)
    op.add_column(
        "workspaces",
        sa.Column("parts_provider_api_key", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "parts_provider_api_key")
    op.drop_column("workspaces", "parts_provider")
