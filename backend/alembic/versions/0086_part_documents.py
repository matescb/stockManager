"""Part documents: `part_datasheets.kind`, `.title`, `.origin` (SM-016).

`part_datasheets` already is the local store for one part's PDF keyed by
the URL it came from — the record that makes the datasheet backfill
idempotent and resumable (ADR-0033, migration 0079). SM-016 widens what
the same hourly cron job downloads beyond the datasheet: DigiKey's
product `MediaLinks` (application notes, technical notes, PCB design
notes, programming manuals, PCNs) and documents an agent found on a
manufacturer site and queued with the `request_part_document` MCP tool.
ADR-0036 records the decision.

Those are the same shape of thing — one PDF, one part, one source URL,
the same retry / failure bookkeeping — so they get rows in the same
table rather than a sibling table with a copy of every column:

* ``kind`` — what the document is: ``datasheet`` (every existing row,
  hence the server default), ``application_note``, ``technical_note``,
  ``design_note``, ``programming_manual``, ``pcn``, ``other``. Validated
  in code (`domain/parts/services/documents.py::DOCUMENT_KINDS`) AND by a
  CHECK, matching how 0079 pinned ``status``.
* ``title`` — the human title DigiKey or the agent gave it. Nullable.
* ``origin`` — why the row exists, which is what tells the two halves of
  the backfill apart: ``datasheet_field`` (the part's ``datasheet_url``
  custom field — every existing row, hence the server default),
  ``media_link`` (queued from a provider record on create / refresh) or
  ``agent`` (queued by `request_part_document`). The datasheet half
  keeps selecting candidates from the custom field exactly as before;
  the document half selects ``origin <> 'datasheet_field'`` rows.
* ``status`` gains ``pending`` — queued, never attempted. 0079's CHECK
  allowed only ``stored`` / ``failed`` (the datasheet half creates its row
  at the moment it fetches), so it is dropped and re-added wider.

The idempotency key (`uq_part_datasheets_ws_part_url`) is unchanged: one
row per (workspace, part, source URL) whatever its kind, so an agent
re-queuing the part's own datasheet URL finds the existing row.

**Locks.** Three `ADD COLUMN`s; the two NOT NULL ones carry a constant
server default, which Postgres 11+ stores in the catalog without
rewriting the table. The three CHECKs are validated by one scan each
under ACCESS EXCLUSIVE — the table is small (one row per datasheet, ~260
in prod), so that is milliseconds.

Reversibility: total for datasheets. `downgrade()` deletes the rows 0079
cannot describe — every ``pending`` row (0079's CHECK has no such status)
and every non-datasheet ``kind`` — restores 0079's status CHECK and drops
the three columns. It deliberately does NOT delete by ``origin``: a real
datasheet row can carry ``origin = 'agent'`` or ``'media_link'`` (an agent
queued the part's own datasheet URL, and the datasheet half later reuses
that row by URL and stores it), and those records must survive a
rollback. Deleted documents' attachments stay: they are ordinary part
attachments and the part still lists them.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "part_datasheets",
        sa.Column(
            "kind",
            sa.String(length=40),
            nullable=False,
            server_default=sa.text("'datasheet'"),
        ),
    )
    op.add_column(
        "part_datasheets",
        sa.Column("title", sa.String(length=300), nullable=True),
    )
    op.add_column(
        "part_datasheets",
        sa.Column(
            "origin",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'datasheet_field'"),
        ),
    )
    op.drop_constraint("ck_part_datasheets_status", "part_datasheets", type_="check")
    op.create_check_constraint(
        "ck_part_datasheets_status",
        "part_datasheets",
        "status IN ('pending', 'stored', 'failed')",
    )
    op.create_check_constraint(
        "ck_part_datasheets_kind",
        "part_datasheets",
        "kind IN ('datasheet', 'application_note', 'technical_note', "
        "'design_note', 'programming_manual', 'pcn', 'other')",
    )
    op.create_check_constraint(
        "ck_part_datasheets_origin",
        "part_datasheets",
        "origin IN ('datasheet_field', 'media_link', 'agent')",
    )
    # The document backfill's candidate scan and the per-workspace
    # pending-request cap both filter on origin within a workspace.
    op.create_index(
        "ix_part_datasheets_ws_origin_status",
        "part_datasheets",
        ["workspace_id", "origin", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_part_datasheets_ws_origin_status", table_name="part_datasheets")
    op.execute(
        "DELETE FROM part_datasheets WHERE status = 'pending' OR kind <> 'datasheet'"
    )
    op.drop_constraint("ck_part_datasheets_origin", "part_datasheets", type_="check")
    op.drop_constraint("ck_part_datasheets_kind", "part_datasheets", type_="check")
    op.drop_constraint("ck_part_datasheets_status", "part_datasheets", type_="check")
    op.create_check_constraint(
        "ck_part_datasheets_status", "part_datasheets", "status IN ('stored', 'failed')"
    )
    op.drop_column("part_datasheets", "origin")
    op.drop_column("part_datasheets", "title")
    op.drop_column("part_datasheets", "kind")
