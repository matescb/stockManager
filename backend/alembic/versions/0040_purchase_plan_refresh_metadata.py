"""Add purchase plan refresh metadata.

Revision ID: 0040
Revises: 0039
Create Date: 2026-05-09
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "purchase_plans",
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "purchase_plans",
        sa.Column("max_distributors", sa.Integer(), nullable=True),
    )
    op.add_column(
        "purchase_plans",
        sa.Column("moq_overbuy_cap", sa.Integer(), nullable=True),
    )
    op.add_column(
        "purchase_plans",
        sa.Column("price_tolerance_pct", sa.Numeric(8, 4), nullable=True),
    )
    op.create_check_constraint(
        "purchase_plans_max_distributors_positive",
        "purchase_plans",
        "max_distributors IS NULL OR max_distributors >= 1",
    )
    op.create_check_constraint(
        "purchase_plans_moq_overbuy_cap_positive",
        "purchase_plans",
        "moq_overbuy_cap IS NULL OR moq_overbuy_cap >= 1",
    )
    op.create_check_constraint(
        "purchase_plans_price_tolerance_pct_nonnegative",
        "purchase_plans",
        "price_tolerance_pct IS NULL OR price_tolerance_pct >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "purchase_plans_price_tolerance_pct_nonnegative",
        "purchase_plans",
        type_="check",
    )
    op.drop_constraint(
        "purchase_plans_moq_overbuy_cap_positive",
        "purchase_plans",
        type_="check",
    )
    op.drop_constraint(
        "purchase_plans_max_distributors_positive",
        "purchase_plans",
        type_="check",
    )
    op.drop_column("purchase_plans", "price_tolerance_pct")
    op.drop_column("purchase_plans", "moq_overbuy_cap")
    op.drop_column("purchase_plans", "max_distributors")
    op.drop_column("purchase_plans", "last_refreshed_at")
