"""Add workspace active sourcing lists.

Revision ID: 0042
Revises: 0041
Create Date: 2026-05-10
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column(
            "active_currencies",
            JSONB,
            nullable=False,
            server_default=sa.text('\'["EUR","USD","CZK","GBP"]\'::jsonb'),
        ),
    )
    op.add_column(
        "workspaces",
        sa.Column(
            "active_countries",
            JSONB,
            nullable=False,
            server_default=sa.text('\'["CZ","DE","US","GB"]\'::jsonb'),
        ),
    )
    op.add_column(
        "workspaces",
        sa.Column(
            "active_distributors",
            JSONB,
            nullable=False,
            server_default=sa.text('\'["DigiKey","Mouser","Farnell","TME","LCSC"]\'::jsonb'),
        ),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "active_distributors")
    op.drop_column("workspaces", "active_countries")
    op.drop_column("workspaces", "active_currencies")
