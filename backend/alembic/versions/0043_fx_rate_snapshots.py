"""Add global ECB FX rate snapshots.

Revision ID: 0043
Revises: 0042
Create Date: 2026-05-10
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Global public reference data: intentionally no workspace_id column.
    op.create_table(
        "fx_rate_snapshots",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("fetched_date", sa.Date(), nullable=False),
        sa.Column("rates", JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "fetched_date",
            name="uq_fx_rate_snapshots_fetched_date",
        ),
    )


def downgrade() -> None:
    op.drop_table("fx_rate_snapshots")
