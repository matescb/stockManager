"""Tune password reset request autovacuum settings.

Revision ID: 0063
Revises: 0062
Create Date: 2026-05-15

AUD-091 / issue #748.

This follows 0062 from PR #760, preserving a single Alembic head.
"""

from __future__ import annotations

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE password_reset_requests SET (
            autovacuum_vacuum_scale_factor = 0.05,
            autovacuum_analyze_scale_factor = 0.05,
            autovacuum_vacuum_threshold = 1000
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE password_reset_requests RESET (
            autovacuum_vacuum_scale_factor,
            autovacuum_analyze_scale_factor,
            autovacuum_vacuum_threshold
        )
        """
    )
