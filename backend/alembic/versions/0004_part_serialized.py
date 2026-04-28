"""parts.serialized

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-28
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default lets us add the column NOT NULL on a non-empty table;
    # dropping it afterward keeps the SQLAlchemy default in charge of new rows.
    op.add_column(
        'parts',
        sa.Column('serialized', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column('parts', 'serialized', server_default=None)


def downgrade() -> None:
    op.drop_column('parts', 'serialized')
