"""Add part categories + parts.category_id.

Revision ID: 0067
Revises: 0066
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "part_categories",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("refdes_prefix", sa.String(length=10), nullable=True),
        sa.Column("default_symbol_ref", sa.String(length=200), nullable=True),
        sa.Column("default_footprint_ref", sa.String(length=200), nullable=True),
        sa.Column(
            "footprint_filters",
            postgresql.ARRAY(sa.String(length=100)),
            nullable=True,
        ),
        sa.Column("library_slug", sa.String(length=60), nullable=False),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", sa.UUID(as_uuid=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_part_categories_workspace_id",
        "part_categories",
        ["workspace_id"],
    )
    op.create_index(
        "ix_part_categories_archived_at",
        "part_categories",
        ["archived_at"],
    )
    op.create_index(
        "ix_part_categories_ws_archived",
        "part_categories",
        ["workspace_id", "archived_at"],
    )
    # Name and slug are unique per workspace among ACTIVE rows only —
    # archiving a category frees both for re-use (same shape as
    # `uq_tag_ws_name`, alembic 0018).
    op.create_index(
        "uq_part_categories_ws_name",
        "part_categories",
        ["workspace_id", "name"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.create_index(
        "uq_part_categories_ws_slug",
        "part_categories",
        ["workspace_id", "library_slug"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )

    op.add_column(
        "parts",
        sa.Column("category_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_parts_category_id",
        "parts",
        "part_categories",
        ["category_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Partial: almost every existing row has NULL category_id; indexing
    # only real assignments keeps the index tiny (matches the model's
    # __table_args__ predicate).
    op.create_index(
        "ix_parts_category_id",
        "parts",
        ["category_id"],
        postgresql_where=sa.text("category_id IS NOT NULL"),
    )

    # Defence-in-depth workspace guard, mirroring 0036's
    # parts_default_storage_workspace_check but with the modern WS001
    # SQLSTATE (0060/0064) so raise_integrity_as_409 maps it to a 409.
    # The application layer already validates via assert_in_workspace;
    # this stops raw SQL from smuggling a foreign category_id.
    op.execute("""
    CREATE OR REPLACE FUNCTION check_parts_category_workspace() RETURNS trigger AS $$
    BEGIN
      IF NEW.category_id IS NOT NULL THEN
        PERFORM 1 FROM part_categories
        WHERE id = NEW.category_id
          AND workspace_id = NEW.workspace_id;
        IF NOT FOUND THEN
          RAISE EXCEPTION 'parts.category_id (%) not in workspace (%)',
            NEW.category_id, NEW.workspace_id
            USING ERRCODE = 'WS001';
        END IF;
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("""
    CREATE TRIGGER parts_category_workspace_check
      BEFORE INSERT OR UPDATE OF category_id, workspace_id
      ON parts
      FOR EACH ROW
      EXECUTE FUNCTION check_parts_category_workspace();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS parts_category_workspace_check ON parts;")
    op.execute("DROP FUNCTION IF EXISTS check_parts_category_workspace();")
    # Drop the referencing column before the table it points at.
    op.drop_index("ix_parts_category_id", table_name="parts")
    op.drop_constraint("fk_parts_category_id", "parts", type_="foreignkey")
    op.drop_column("parts", "category_id")

    op.drop_index("uq_part_categories_ws_slug", table_name="part_categories")
    op.drop_index("uq_part_categories_ws_name", table_name="part_categories")
    op.drop_index("ix_part_categories_ws_archived", table_name="part_categories")
    op.drop_index("ix_part_categories_archived_at", table_name="part_categories")
    op.drop_index("ix_part_categories_workspace_id", table_name="part_categories")
    op.drop_table("part_categories")
