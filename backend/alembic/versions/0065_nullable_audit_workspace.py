"""Allow workspace-less audit rows for auth/system events.

Revision ID: 0065
Revises: 0064
Create Date: 2026-05-15

AUD-094 / issue #751.
"""

from __future__ import annotations

from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "audit_log",
        "workspace_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    op.execute("DELETE FROM audit_log WHERE workspace_id IS NULL")
    op.alter_column(
        "audit_log",
        "workspace_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
