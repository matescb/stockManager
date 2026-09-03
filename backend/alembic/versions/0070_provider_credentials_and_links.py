"""Add workspace_provider_credentials and part_provider_links.

Two additive tables that let a workspace configure a SECOND parts
provider alongside the one in `workspaces.parts_provider`. Nothing
existing is dropped or altered: the legacy
`workspaces.parts_provider_api_key` / `_api_secret` columns and the
`parts.linked_*` columns stay exactly where they are and keep serving
the primary provider.

`part_provider_links` is backfilled: every part with `linked_provider`
set gets a link row carrying its `linked_external_id` and
`last_refresh_at`, so the table answers "which providers know this part"
for the whole existing catalog, not just for parts refreshed after
deploy.

`workspace_provider_credentials` is deliberately NOT backfilled. It
holds SECONDARY providers only. Copying the primary's key into it would
leave one provider with two credential stores that nothing keeps in
sync: `PATCH /api/workspaces/current` writes the columns, `PUT
/api/workspaces/current/provider-credentials` writes the row, and
clearing either one reports success while the other keeps
authenticating. It would also re-arm a provider a workspace had turned
off — a workspace that set `parts_provider` back to `none` still has its
old key in the legacy columns, and a backfilled row would make
`?provider=<that one>` keep working. The route now refuses a payload
naming the primary, and this table starts empty.

Downgrades drop both tables. There is deliberately no reverse backfill:
the legacy columns were never touched, so dropping these tables loses
only the *second* provider's configuration, which has no pre-0070 home
to be written back to.

No BEFORE triggers, for the same reason as 0068: nothing writes either
table outside its service, whose cross-table lookups all go through
`assert_in_workspace`. Isolation stays code-enforced and is pinned by
`tests/test_workspace_isolation.py`.

Revision ID: 0070
Revises: 0069
Create Date: 2026-09-03
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def _workspace_owned_columns() -> list[sa.Column]:
    """The `WorkspaceOwned` mixin's columns, spelled out.

    A migration must not import `app.domain._mixins`
    (`tests/test_migration_isolation.py`) — it has to describe the schema
    as it was the day it ran, not as the model later becomes. Same
    verbatim block as 0068.
    """
    return [
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", sa.UUID(as_uuid=True), nullable=False),
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
    ]


def _workspace_owned_constraints() -> list[sa.schema.SchemaItem]:
    return [
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    ]


def upgrade() -> None:
    op.create_table(
        "workspace_provider_credentials",
        *_workspace_owned_columns(),
        sa.Column("provider", sa.String(length=40), nullable=False),
        # Fernet ciphertext (app.core.secrets), never plaintext.
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("api_secret_encrypted", sa.Text(), nullable=True),
        *_workspace_owned_constraints(),
    )
    op.create_index(
        "ix_workspace_provider_credentials_workspace_id",
        "workspace_provider_credentials",
        ["workspace_id"],
    )
    op.create_index(
        "ix_workspace_provider_credentials_archived_at",
        "workspace_provider_credentials",
        ["archived_at"],
    )
    # One live credential per (workspace, provider); archiving frees the slot.
    op.create_index(
        "uq_workspace_provider_credentials_ws_provider",
        "workspace_provider_credentials",
        ["workspace_id", "provider"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )

    op.create_table(
        "part_provider_links",
        *_workspace_owned_columns(),
        sa.Column("part_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("external_id", sa.String(length=300), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["part_id"], ["parts.id"], ondelete="CASCADE"),
        *_workspace_owned_constraints(),
    )
    op.create_index(
        "ix_part_provider_links_workspace_id", "part_provider_links", ["workspace_id"]
    )
    op.create_index(
        "ix_part_provider_links_archived_at", "part_provider_links", ["archived_at"]
    )
    op.create_index("ix_part_provider_links_part_id", "part_provider_links", ["part_id"])
    op.create_index(
        "uq_part_provider_links_part_provider",
        "part_provider_links",
        ["part_id", "provider"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    # Serves "which parts does provider X know about, in this workspace".
    op.create_index(
        "ix_part_provider_links_ws_provider",
        "part_provider_links",
        ["workspace_id", "provider"],
    )

    # ---- Backfill --------------------------------------------------
    # Links only. `workspace_provider_credentials` starts empty on
    # purpose — see the module docstring.
    op.execute(
        sa.text(
            """
            INSERT INTO part_provider_links
                (id, workspace_id, part_id, provider, external_id,
                 last_refresh_at, created_at, updated_at)
            SELECT gen_random_uuid(), p.workspace_id, p.id, p.linked_provider,
                   p.linked_external_id, p.last_refresh_at, now(), now()
              FROM parts p
             WHERE p.linked_provider IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_part_provider_links_ws_provider", table_name="part_provider_links")
    op.drop_index("uq_part_provider_links_part_provider", table_name="part_provider_links")
    op.drop_index("ix_part_provider_links_part_id", table_name="part_provider_links")
    op.drop_index("ix_part_provider_links_archived_at", table_name="part_provider_links")
    op.drop_index("ix_part_provider_links_workspace_id", table_name="part_provider_links")
    op.drop_table("part_provider_links")

    op.drop_index(
        "uq_workspace_provider_credentials_ws_provider",
        table_name="workspace_provider_credentials",
    )
    op.drop_index(
        "ix_workspace_provider_credentials_archived_at",
        table_name="workspace_provider_credentials",
    )
    op.drop_index(
        "ix_workspace_provider_credentials_workspace_id",
        table_name="workspace_provider_credentials",
    )
    op.drop_table("workspace_provider_credentials")
