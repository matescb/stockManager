"""Add short-lived purchase plans.

Revision ID: 0039
Revises: 0038
Create Date: 2026-05-08
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "purchase_plans",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("build_quantity", sa.Integer(), nullable=False),
        sa.Column("strategy", sa.String(length=40), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("currency_code", sa.String(length=3), nullable=True),
        sa.Column("preferred_distributors", JSONB, nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="draft",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "build_quantity >= 1",
            name="purchase_plans_build_quantity_positive",
        ),
        sa.CheckConstraint(
            "strategy IN ("
            "'lowest_total_price', "
            "'fewest_distributors', "
            "'fastest_availability', "
            "'preferred_first'"
            ")",
            name="purchase_plans_strategy_check",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'refreshed', 'converted', 'expired')",
            name="purchase_plans_status_check",
        ),
        sa.CheckConstraint(
            "expires_at <= created_at + interval '7 days'",
            name="purchase_plans_max_7_day_ttl",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_purchase_plans_expires_at",
        "purchase_plans",
        ["expires_at"],
    )
    op.create_index(
        "ix_purchase_plans_ws_project",
        "purchase_plans",
        ["workspace_id", "project_id"],
    )
    op.create_index(
        "ix_purchase_plans_ws_status",
        "purchase_plans",
        ["workspace_id", "status"],
    )

    op.create_table(
        "purchase_plan_lines",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("purchase_plan_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("project_entry_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("part_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("mpn_searched", sa.String(length=255), nullable=False),
        sa.Column("required_qty", sa.Integer(), nullable=False),
        sa.Column("internal_available_qty", sa.Integer(), nullable=False),
        sa.Column("shortage_qty", sa.Integer(), nullable=False),
        sa.Column("selected_distributor", sa.String(length=120), nullable=True),
        sa.Column("selected_qty", sa.Integer(), nullable=True),
        sa.Column("selected_unit_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("selected_currency", sa.String(length=3), nullable=True),
        sa.Column("selected_packaging", sa.String(length=120), nullable=True),
        sa.Column("selected_moq", sa.Integer(), nullable=True),
        sa.Column("selected_lead_time_days", sa.Integer(), nullable=True),
        sa.Column("selected_url", sa.Text(), nullable=True),
        sa.Column(
            "risk_flags",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "required_qty >= 0",
            name="purchase_plan_lines_required_qty_nonnegative",
        ),
        sa.CheckConstraint(
            "internal_available_qty >= 0",
            name="purchase_plan_lines_internal_available_qty_nonnegative",
        ),
        sa.CheckConstraint(
            "shortage_qty >= 0",
            name="purchase_plan_lines_shortage_qty_nonnegative",
        ),
        sa.CheckConstraint(
            "selected_qty IS NULL OR selected_qty >= 0",
            name="purchase_plan_lines_selected_qty_nonnegative",
        ),
        sa.CheckConstraint(
            "selected_moq IS NULL OR selected_moq >= 1",
            name="purchase_plan_lines_selected_moq_positive",
        ),
        sa.ForeignKeyConstraint(
            ["part_id"],
            ["parts.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_plan_id"],
            ["purchase_plans.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_purchase_plan_lines_plan",
        "purchase_plan_lines",
        ["purchase_plan_id"],
    )
    op.create_index(
        "ix_purchase_plan_lines_part",
        "purchase_plan_lines",
        ["part_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_purchase_plan_lines_part", table_name="purchase_plan_lines")
    op.drop_index("ix_purchase_plan_lines_plan", table_name="purchase_plan_lines")
    op.drop_table("purchase_plan_lines")

    op.drop_index("ix_purchase_plans_ws_status", table_name="purchase_plans")
    op.drop_index("ix_purchase_plans_ws_project", table_name="purchase_plans")
    op.drop_index("ix_purchase_plans_expires_at", table_name="purchase_plans")
    op.drop_table("purchase_plans")
