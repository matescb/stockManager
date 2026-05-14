"""Add purchase plan line project entry foreign key.

Revision ID: 0051
Revises: 0050
Create Date: 2026-05-14
"""

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None

CONSTRAINT_NAME = "fk_purchase_plan_lines_project_entry_id_project_entries"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE purchase_plan_lines
        ADD CONSTRAINT {CONSTRAINT_NAME}
        FOREIGN KEY (project_entry_id)
        REFERENCES project_entries (id)
        ON DELETE SET NULL
        NOT VALID
        """
    )
    op.execute(
        f"""
        ALTER TABLE purchase_plan_lines
        VALIDATE CONSTRAINT {CONSTRAINT_NAME}
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        CONSTRAINT_NAME,
        "purchase_plan_lines",
        type_="foreignkey",
    )
