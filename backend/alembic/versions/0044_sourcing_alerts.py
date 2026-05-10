"""Add sourcing alerts.

Revision ID: 0044
Revises: 0043
Create Date: 2026-05-10
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sourcing_alerts",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("part_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("project_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("alert_type", sa.String(length=40), nullable=False),
        sa.Column("threshold", JSONB, nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("currency_code", sa.String(length=3), nullable=True),
        sa.Column("distributor_filter", JSONB, nullable=True),
        sa.Column("notify_user_ids", JSONB, nullable=True),
        sa.Column(
            "cooldown_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("86400"),
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_evaluation_state", JSONB, nullable=True),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=True),
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
        sa.CheckConstraint(
            "alert_type IN ("
            "'stock_below', "
            "'stock_above', "
            "'back_in_stock', "
            "'out_of_authorized_stock', "
            "'price_changed', "
            "'bom_buyable'"
            ")",
            name="sourcing_alerts_alert_type_check",
        ),
        sa.CheckConstraint(
            "cooldown_seconds >= 60",
            name="sourcing_alerts_cooldown_seconds_min",
        ),
        sa.CheckConstraint(
            "(part_id IS NOT NULL) <> (project_id IS NOT NULL)",
            name="sourcing_alerts_part_project_xor",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["part_id"],
            ["parts.id"],
            ondelete="CASCADE",
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
        "uq_sourcing_alerts_active_target_threshold",
        "sourcing_alerts",
        [
            "workspace_id",
            "alert_type",
            sa.text("COALESCE(part_id, project_id)"),
            "threshold",
        ],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.create_index(
        "ix_sourcing_alerts_ws_enabled_archived",
        "sourcing_alerts",
        ["workspace_id", "enabled", "archived_at"],
    )
    op.create_index(
        "ix_sourcing_alerts_last_checked_at",
        "sourcing_alerts",
        ["last_checked_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_sourcing_alerts_last_checked_at", table_name="sourcing_alerts")
    op.drop_index(
        "ix_sourcing_alerts_ws_enabled_archived",
        table_name="sourcing_alerts",
    )
    op.drop_index(
        "uq_sourcing_alerts_active_target_threshold",
        table_name="sourcing_alerts",
    )
    op.drop_table("sourcing_alerts")
