"""Drop unused password reset expires_at index.

Revision ID: 0062
Revises: 0061
Create Date: 2026-05-15

AUD-083 / issue #740.

Password reset retention purges by created_at because throttled and
non-issued rows can have expires_at NULL. The created_at index remains the
retention path; the expires_at index is intentionally removed.
Runbook: docs/runbooks/migration-recovery.md#downgrading-through-migration-0062-after-manually-recreating-the-password-reset-index.
"""
from __future__ import annotations

from alembic import op


revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_password_reset_requests_expires_at", table_name="password_reset_requests")


def downgrade() -> None:
    op.create_index(
        "ix_password_reset_requests_expires_at",
        "password_reset_requests",
        ["expires_at"],
    )
