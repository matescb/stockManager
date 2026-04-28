"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-26
"""
from alembic import op

from app.infra.db import Base
import app.domain.all_models  # noqa: F401  registers tables


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
