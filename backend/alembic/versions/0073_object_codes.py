"""Add object_codes (universal short codes for scannable objects).

Track A1 — every part / lot / storage location / order / build in a
workspace can carry one short, human-transcribable code. Scanning or
typing it resolves back to that exact object, which is what makes
"ID-Anything" style labelling possible. Label rendering and printing are
a later PR; this is the code system + resolver only.

One central polymorphic table rather than a `code` column on five tables:
uniqueness has a single scope (`uq_object_codes_ws_code`), the resolver
is one query instead of a growing UNION, and rows that never get labelled
never pay for a column. `entity_id` therefore carries no FK — the same
trade-off `attachments` / `custom_fields` / `tag_links` make — and
hard-delete cleanup runs through
`app/domain/_polymorphic_cleanup.py`, which registers this table
alongside those three.

`entity_type` is CHECK-constrained to a closed set. Unlike the other
polymorphic tables the codeable set is deliberately not open-ended: a
code is a physical-world handle, so `project` is absent.

Revision ID: 0073
Revises: 0072
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "object_codes",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        # Polymorphic, un-constrained pointer — no FK (see module docstring).
        sa.Column("entity_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "entity_type IN ('build', 'lot', 'order', 'part', 'storage_location')",
            name="ck_object_codes_entity_type",
        ),
        # CASCADE: a deleted workspace takes its codes with it.
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        # The resolver's index. Codes are unique PER WORKSPACE, so two
        # workspaces may independently mint the same string and the code
        # itself can stay short.
        sa.UniqueConstraint("workspace_id", "code", name="uq_object_codes_ws_code"),
        # One code per object, forever. Also what makes the get-or-create
        # mint safe under concurrency: the losing INSERT re-reads the
        # winner's code instead of allocating a second one.
        sa.UniqueConstraint(
            "workspace_id",
            "entity_type",
            "entity_id",
            name="uq_object_codes_ws_entity",
        ),
    )
    op.create_index("ix_object_codes_workspace_id", "object_codes", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_object_codes_workspace_id", table_name="object_codes")
    op.drop_table("object_codes")
