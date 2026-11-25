"""Per-version metadata and review state for hosted EDA files (SM-010).

One new table, `eda_asset_metadata`: for one hosted symbol, footprint,
3D model or SPICE file AND one `sha256` of it, where the file came from
(`origin`), whether a SPICE model is vendor-supplied, converted or
fitted (`model_kind`), the simulators it was checked with, the analyses
it is and is not valid for, a free `note`, and a review state with
reviewer, time and what it was checked against.

Keyed by asset id + sha256 so a new file version (new bytes, same row)
reads as `unreviewed` with no metadata: the lookup for the new hash
simply misses. Rows for older hashes are kept as history.

`source` on the asset tables is NOT touched — it stays the server-set
import channel.

**Additive only.** One new table, no backfill; no existing row is read
or rewritten. An asset with no metadata row reads as `unreviewed`.

**Locks.** CREATE TABLE plus its indexes on an empty table; the FKs take
SHARE ROW EXCLUSIVE on `eda_symbols` / `eda_footprints` /
`eda_datafiles` / `workspaces` / `users` for the instant of the DDL only.

No workspace BEFORE trigger, for the reason 0068 gives for the other EDA
tables: the asset FK is only ever resolved through the part's own
workspace-filtered `part_eda` row in the one module that writes it.

Reversibility: total — `downgrade()` drops the table (and with it the
recorded metadata and reviews).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None

_TABLE = "eda_asset_metadata"


def _uuid_fk(name: str, target: str, ondelete: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(target, ondelete=ondelete),
        nullable=True,
    )


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _uuid_fk("symbol_id", "eda_symbols.id", "CASCADE"),
        _uuid_fk("footprint_id", "eda_footprints.id", "CASCADE"),
        _uuid_fk("datafile_id", "eda_datafiles.id", "CASCADE"),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("origin", sa.String(length=1000), nullable=True),
        sa.Column("model_kind", sa.String(length=12), nullable=True),
        sa.Column("simulators", postgresql.ARRAY(sa.String(length=40)), nullable=True),
        sa.Column("valid_for", postgresql.ARRAY(sa.String(length=120)), nullable=True),
        sa.Column("not_valid_for", postgresql.ARRAY(sa.String(length=120)), nullable=True),
        sa.Column("note", sa.String(length=2000), nullable=True),
        sa.Column(
            "review_state",
            sa.String(length=12),
            nullable=False,
            server_default=sa.text("'unreviewed'"),
        ),
        _uuid_fk("reviewed_by", "users.id", "SET NULL"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_against", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _uuid_fk("created_by", "users.id", "SET NULL"),
        _uuid_fk("updated_by", "users.id", "SET NULL"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "num_nonnulls(symbol_id, footprint_id, datafile_id) = 1",
            name="ck_eda_asset_metadata_one_asset",
        ),
        sa.CheckConstraint(
            "model_kind IS NULL OR model_kind IN ('vendor', 'converted', 'fitted')",
            name="ck_eda_asset_metadata_model_kind",
        ),
        sa.CheckConstraint(
            "review_state IN ('unreviewed', 'reviewed', 'rejected')",
            name="ck_eda_asset_metadata_review_state",
        ),
    )
    op.create_index("ix_eda_asset_metadata_workspace_id", _TABLE, ["workspace_id"])
    op.create_index("ix_eda_asset_metadata_archived_at", _TABLE, ["archived_at"])
    for col in ("symbol_id", "footprint_id", "datafile_id"):
        stem = col.removesuffix("_id")
        op.create_index(
            f"uq_eda_asset_metadata_{stem}_sha",
            _TABLE,
            [col, "sha256"],
            unique=True,
            postgresql_where=sa.text(f"{col} IS NOT NULL"),
        )


def downgrade() -> None:
    for stem in ("datafile", "footprint", "symbol"):
        op.drop_index(f"uq_eda_asset_metadata_{stem}_sha", table_name=_TABLE)
    op.drop_index("ix_eda_asset_metadata_archived_at", table_name=_TABLE)
    op.drop_index("ix_eda_asset_metadata_workspace_id", table_name=_TABLE)
    op.drop_table(_TABLE)
