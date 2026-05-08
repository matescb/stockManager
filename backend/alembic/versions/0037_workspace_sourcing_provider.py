"""Add workspace sourcing provider settings.

Revision ID: 0037
Revises: 0036
Create Date: 2026-05-08
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column(
            "sourcing_provider",
            sa.String(length=40),
            nullable=False,
            server_default="none",
        ),
    )
    op.add_column(
        "workspaces",
        sa.Column("sourcing_company_id_enc", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "workspaces",
        sa.Column("sourcing_api_key_enc", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "workspaces",
        sa.Column("sourcing_country_code", sa.String(length=2), nullable=True),
    )
    op.add_column(
        "workspaces",
        sa.Column("sourcing_currency_code", sa.String(length=3), nullable=True),
    )
    op.add_column(
        "workspaces",
        sa.Column("sourcing_preferred_distributors", JSONB, nullable=True),
    )
    op.add_column(
        "workspaces",
        sa.Column(
            "sourcing_use_cached_for_dashboards",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "sourcing_use_cached_for_dashboards")
    op.drop_column("workspaces", "sourcing_preferred_distributors")
    op.drop_column("workspaces", "sourcing_currency_code")
    op.drop_column("workspaces", "sourcing_country_code")
    op.drop_column("workspaces", "sourcing_api_key_enc")
    op.drop_column("workspaces", "sourcing_company_id_enc")
    op.drop_column("workspaces", "sourcing_provider")
