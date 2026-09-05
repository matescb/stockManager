"""Add label_templates (reusable label layouts rendered to cab JScript).

Track A3. A label template is the stock geometry (what the media physically
is) plus an ordered list of placed elements (what gets drawn on it). The
renderer, `app/domain/printing/label_render.py`, turns one plus a binding
context into a complete JScript program for the cab SQUIX driver vendored in
#890, and every label carries the object code minted by #892.

Why geometry gets columns and elements gets JSONB: the renderer needs the
geometry to build the JScript `H`/`S` job header BEFORE it looks at a single
element, and ops will want to query "which templates are on 50x30 stock" — so
those are columns. The element list is read and written whole by the renderer
and (later) the designer, and is never queried element-by-element, so a
document column is the honest shape rather than a child table nothing joins.

`entity_type` is CHECK-constrained to the same closed set as `object_codes`,
not a second list: a label carries a code, so a type you cannot mint a code
for is a type you cannot label.

One default per (workspace, entity_type) is a PARTIAL unique index — partial
so any number of NON-default templates per type coexist. Promoting a template
demotes the incumbent in the same transaction
(`template_service.clear_existing_default`); a bare flip would hit this index.

No data backfill. The built-in defaults live in Python
(`app/domain/printing/default_templates.py`) and are materialised per workspace
by `POST /api/label-templates/defaults`, so the catalog exists once and a
workspace created after this migration gets the same defaults as one created
before it.

Revision ID: 0076
Revises: 0075
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "label_templates",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        # Stock geometry. Numeric(6,2) — tenths of a millimetre are already
        # finer than the media tolerance, and float would make "50.00 mm"
        # compare unequal to itself across a round-trip.
        sa.Column("width_mm", sa.Numeric(6, 2), nullable=False),
        sa.Column("height_mm", sa.Numeric(6, 2), nullable=False),
        sa.Column(
            "gap_mm", sa.Numeric(6, 2), nullable=False, server_default=sa.text("3")
        ),
        sa.Column("heat", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("speed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # 'T' = thermal transfer (ribbon), 'D' = direct thermal.
        sa.Column(
            "method", sa.String(length=1), nullable=False, server_default=sa.text("'T'")
        ),
        sa.Column("dpi", sa.Integer(), nullable=False, server_default=sa.text("300")),
        sa.Column(
            "is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "elements",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # WorkspaceOwned mixin columns.
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        # Same closed set as object_codes (migration 0073) — kept literal here
        # because a migration must not import runtime constants that may move.
        sa.CheckConstraint(
            "entity_type IN ('build', 'lot', 'order', 'part', 'storage_location')",
            name="ck_label_templates_entity_type",
        ),
        sa.CheckConstraint("method IN ('T', 'D')", name="ck_label_templates_method"),
        # CASCADE: a deleted workspace takes its label templates with it.
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_label_templates_workspace_id", "label_templates", ["workspace_id"]
    )
    op.create_index(
        "ix_label_templates_archived_at", "label_templates", ["archived_at"]
    )
    # The list endpoint's access path.
    op.create_index(
        "ix_label_templates_ws_entity",
        "label_templates",
        ["workspace_id", "entity_type"],
    )
    # At most one default per (workspace, entity_type). PARTIAL: non-default
    # templates of the same type are unconstrained.
    op.create_index(
        "uq_label_templates_ws_default",
        "label_templates",
        ["workspace_id", "entity_type"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )


def downgrade() -> None:
    op.drop_index("uq_label_templates_ws_default", table_name="label_templates")
    op.drop_index("ix_label_templates_ws_entity", table_name="label_templates")
    op.drop_index("ix_label_templates_archived_at", table_name="label_templates")
    op.drop_index("ix_label_templates_workspace_id", table_name="label_templates")
    op.drop_table("label_templates")
