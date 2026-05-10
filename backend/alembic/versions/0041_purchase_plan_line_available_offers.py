"""Add cached offers to purchase plan lines.

Revision ID: 0041
Revises: 0040
Create Date: 2026-05-10
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "purchase_plan_lines",
        sa.Column(
            "available_offers",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("purchase_plan_lines", "available_offers")
