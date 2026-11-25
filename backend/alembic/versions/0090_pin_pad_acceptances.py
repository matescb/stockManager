"""Per-part acceptance of pin/pad findings: `pin_pad_acceptances` (SM-008b).

The pin/pad check (SM-008) is computed on demand and stores nothing. Its
first real run showed findings a human has to look at once and then
should not see again: an exposed pad the symbol leaves out, an NC
package pin, a connector's shell tabs. This table records that look —
one row per (part, finding kind, number) PER PAIR OF FILES:

* ``symbol_sha256`` / ``footprint_sha256`` are the content hashes the
  finding was seen on. Replace either file and the row no longer
  applies; the check reports it as stale rather than silently excusing
  the same number on files nobody has looked at. That is why the unique
  key includes both hashes: accepting again on the same files updates
  the row, accepting on new files adds one.
* ``finding_kind`` — ``pad_without_pin`` / ``pin_without_pad`` /
  ``duplicate_pin``; ``finding_class`` — the class the finding had when
  accepted (``exposed_pad``, ``nc_pin``, ``shell``, ``mechanical``,
  ``electrical``). Both pinned by CHECKs and validated in code. The
  column is not named ``class``: that is a reserved word.
* ``accepted_by`` / ``accepted_at`` / ``note`` — who, when, why. An
  ``electrical`` finding can be accepted only with a note (in code).

The workspace FK trigger follows the 0064 / 0079 contract — validate
``part_id`` against ``workspace_id`` on INSERT and when either changes,
SQLSTATE ``WS001``.

Hard delete: CASCADE from ``parts`` and ``workspaces``; an acceptance
means nothing without its part. ``accepted_by`` is SET NULL.

**Revision chain.** Developed on top of ``0086``. SM-009 (0087), SM-010
(0088) and SM-012 (0089) are in flight; once they merge, ``down_revision``
becomes ``"0089"`` before this one merges.

Locks: a new, empty table — nothing to scan.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0090"
down_revision = "0086"
branch_labels = None
depends_on = None


_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION check_pin_pad_acceptances_workspace_fks()
RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'INSERT' OR NEW.part_id IS DISTINCT FROM OLD.part_id
     OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id THEN
    PERFORM 1 FROM parts
     WHERE id = NEW.part_id
       AND workspace_id = NEW.workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'pin_pad_acceptances.part_id (%) not in workspace (%)',
        NEW.part_id, NEW.workspace_id
        USING ERRCODE = 'WS001';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "pin_pad_acceptances",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("part_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol_sha256", sa.String(length=64), nullable=False),
        sa.Column("footprint_sha256", sa.String(length=64), nullable=False),
        sa.Column("finding_kind", sa.String(length=20), nullable=False),
        sa.Column("number", sa.String(length=100), nullable=False),
        sa.Column("finding_class", sa.String(length=20), nullable=False),
        sa.Column("accepted_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "finding_kind IN ('pad_without_pin', 'pin_without_pad', 'duplicate_pin')",
            name="ck_pin_pad_acceptances_kind",
        ),
        sa.CheckConstraint(
            "finding_class IN ('exposed_pad', 'nc_pin', 'shell', 'mechanical', "
            "'electrical')",
            name="ck_pin_pad_acceptances_class",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["part_id"], ["parts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["accepted_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_pin_pad_acceptances_finding",
        "pin_pad_acceptances",
        [
            "workspace_id",
            "part_id",
            "symbol_sha256",
            "footprint_sha256",
            "finding_kind",
            "number",
        ],
        unique=True,
    )
    op.create_index(
        "ix_pin_pad_acceptances_ws_part",
        "pin_pad_acceptances",
        ["workspace_id", "part_id"],
    )
    op.execute(_TRIGGER_FN)
    op.execute("""
    CREATE TRIGGER pin_pad_acceptances_workspace_fk_check
      BEFORE INSERT OR UPDATE OF workspace_id, part_id
      ON pin_pad_acceptances
      FOR EACH ROW
      EXECUTE FUNCTION check_pin_pad_acceptances_workspace_fks();
    """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS pin_pad_acceptances_workspace_fk_check "
        "ON pin_pad_acceptances;"
    )
    op.execute("DROP FUNCTION IF EXISTS check_pin_pad_acceptances_workspace_fks();")
    op.drop_index("ix_pin_pad_acceptances_ws_part", table_name="pin_pad_acceptances")
    op.drop_index("uq_pin_pad_acceptances_finding", table_name="pin_pad_acceptances")
    op.drop_table("pin_pad_acceptances")
