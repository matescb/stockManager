"""Add part_datasheets — the local datasheet store.

Revision ID: 0079
Revises: 0078
Create Date: 2026-09-09

Until now a part's datasheet was a URL in a `custom_fields` row and nothing
else. Prod measurement: 257 parts carry a `datasheet_url`, 249 of them point
at a manufacturer domain, and only 5 PDFs had ever landed on disk — because
`services/assets.py` refused every host outside the 8-entry Mouser/DigiKey
allow-list. ADR-0033 relaxes that for datasheets; this table is where the
result is recorded.

Shape:

* One row per (workspace, part, source URL). `source_url_sha256` carries the
  unique index because `source_url` is TEXT and a btree over a 2 KB URL
  exceeds Postgres' index-row limit.
* `attachment_id` points at the `attachments` row that makes the stored PDF
  a first-class object. `ON DELETE SET NULL` rather than CASCADE: deleting
  the attachment must not erase the record that we already fetched this URL,
  or the backfill would silently re-download it.
* `part_id` CASCADEs. A datasheet is derived metadata about the part, not
  independent history, so it falls on the "may cascade" side of ADR-0028
  alongside cad keys and provider links. The polymorphic-cleanup listener
  removes the matching `attachments` row in the same hard delete.
* `derived` (JSONB) + `derived_status` are the forward slot for the planned
  Datalab markdown/JSON conversion. The converted artifacts become further
  `attachments` rows on the same part; this document holds the manifest that
  ties them together. That is deliberate: the conversion step should need no
  further migration.

The workspace FK trigger mirrors the 0064 contract — validate every parent
ref on INSERT, only changed refs on UPDATE, SQLSTATE `WS001` so
`raise_integrity_as_409` maps a violation to a 409.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


_PART_DATASHEETS_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION check_part_datasheets_workspace_fks()
RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'INSERT' OR NEW.part_id IS DISTINCT FROM OLD.part_id
     OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id THEN
    PERFORM 1 FROM parts
     WHERE id = NEW.part_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'part_datasheets.part_id (%) not in workspace (%)',
        NEW.part_id, NEW.workspace_id
        USING ERRCODE = 'WS001';
    END IF;
  END IF;

  IF NEW.attachment_id IS NOT NULL
     AND (TG_OP = 'INSERT'
          OR NEW.attachment_id IS DISTINCT FROM OLD.attachment_id
          OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) THEN
    PERFORM 1 FROM attachments
     WHERE id = NEW.attachment_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'part_datasheets.attachment_id (%) not in workspace (%)',
        NEW.attachment_id, NEW.workspace_id
        USING ERRCODE = 'WS001';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "part_datasheets",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("part_id", sa.UUID(as_uuid=True), nullable=False),
        # TEXT, not VARCHAR(n): vendor datasheet URLs carry long signed
        # query strings and truncating one would make the row point at a
        # URL that does not exist.
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_url_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attachment_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("storage_key", sa.String(length=800), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("content_type", sa.String(length=120), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=40), nullable=True),
        sa.Column(
            "derived_status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
        sa.Column(
            "derived",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("derived_updated_at", sa.DateTime(timezone=True), nullable=True),
        # WorkspaceOwned mixin columns.
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('stored', 'failed')", name="ck_part_datasheets_status"
        ),
        sa.CheckConstraint(
            "derived_status IN ('none', 'pending', 'ready', 'failed')",
            name="ck_part_datasheets_derived_status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["part_id"], ["parts.id"], ondelete="CASCADE"),
        # SET NULL, not CASCADE: losing the attachment must not lose the
        # record that this URL was already fetched.
        sa.ForeignKeyConstraint(
            ["attachment_id"], ["attachments.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_part_datasheets_workspace_id", "part_datasheets", ["workspace_id"]
    )
    op.create_index("ix_part_datasheets_archived_at", "part_datasheets", ["archived_at"])
    op.create_index("ix_part_datasheets_part_id", "part_datasheets", ["part_id"])
    op.create_index(
        "ix_part_datasheets_ws_status", "part_datasheets", ["workspace_id", "status"]
    )
    op.create_index(
        "ix_part_datasheets_ws_part", "part_datasheets", ["workspace_id", "part_id"]
    )
    # Backfill idempotency key. Hash rather than the URL itself so the index
    # row stays well inside Postgres' 2704-byte limit.
    op.create_index(
        "uq_part_datasheets_ws_part_url",
        "part_datasheets",
        ["workspace_id", "part_id", "source_url_sha256"],
        unique=True,
    )

    op.execute(_PART_DATASHEETS_TRIGGER_FN)
    op.execute("""
    CREATE TRIGGER part_datasheets_workspace_fk_check
      BEFORE INSERT OR UPDATE OF workspace_id, part_id, attachment_id
      ON part_datasheets
      FOR EACH ROW
      EXECUTE FUNCTION check_part_datasheets_workspace_fks();
    """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS part_datasheets_workspace_fk_check ON part_datasheets;"
    )
    op.execute("DROP FUNCTION IF EXISTS check_part_datasheets_workspace_fks();")

    op.drop_index("uq_part_datasheets_ws_part_url", table_name="part_datasheets")
    op.drop_index("ix_part_datasheets_ws_part", table_name="part_datasheets")
    op.drop_index("ix_part_datasheets_ws_status", table_name="part_datasheets")
    op.drop_index("ix_part_datasheets_part_id", table_name="part_datasheets")
    op.drop_index("ix_part_datasheets_archived_at", table_name="part_datasheets")
    op.drop_index("ix_part_datasheets_workspace_id", table_name="part_datasheets")
    op.drop_table("part_datasheets")
