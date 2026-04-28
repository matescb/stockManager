"""workspace catalog token

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-28
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # catalog_token: nullable, no default — minted by the app when the workspace
    # owner enables the public catalog.
    op.add_column(
        'workspaces',
        sa.Column('catalog_token', sa.String(length=64), nullable=True),
    )
    # catalog_enabled: boolean, NOT NULL, default false. Use server_default so
    # existing rows pick up false on upgrade, then drop it so the SQLAlchemy
    # default takes over for new inserts (mirrors 0004_part_serialized.py).
    op.add_column(
        'workspaces',
        sa.Column('catalog_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column('workspaces', 'catalog_enabled', server_default=None)


def downgrade() -> None:
    op.drop_column('workspaces', 'catalog_enabled')
    op.drop_column('workspaces', 'catalog_token')
