"""Add `custom_fields.provider` and `custom_fields.value_num`.

Revision ID: 0081
Revises: 0079
Create Date: 2026-09-15

Two nullable columns, no backfill, no lock risk — the table is ~9.4k rows
on prod and both adds are metadata-only on Postgres 11+.

* `provider` is provenance. Today a provider-sourced row says only
  `source='provider'`; which provider wrote it is inferred from the key's
  namespace (`provider_fields.py::provider_owns_custom_field_key`). That
  inference works only because a secondary's keys are prefixed. The spec
  schema (ADR-0034) breaks it deliberately: a secondary will write the
  same canonical key a primary writes, so the refresh's "delete every
  `source='provider'` row absent from my payload" pass needs a column it
  can scope on instead of a prefix. Nothing reads it yet — A3 wires it up.

* `value_num` is the numeric sidecar for a parsed spec, in the SI base
  unit (`10 kΩ` -> `10000`). `NUMERIC(36,18)` holds 18 integer and 18
  fractional digits, so a femtofarad and a petaohm are both exact and
  both inside the range — `double precision` is neither. The generous
  scale is deliberate: a value that does not fit is silently rounded by
  Postgres, and picking the width by the units we happen to use today is
  how that happens. It exists so the spec columns can be sorted and
  range-filtered in the database rather than by string.

`String(40)` matches every other provider-name column in the schema
(`workspaces.parts_provider`, `parts.linked_provider`,
`part_provider_links.provider`) rather than inventing a second width.

The partial index is included because it is cheap here — the table is
small and only parsed rows carry a value — and because a sort with no
index over a polymorphic table is the kind of thing that only shows up
once the column is populated. Being partial, it is only usable by a query
that repeats the predicate: A3's spec sort must carry an explicit
`value_num IS NOT NULL`, or Postgres falls back to a seq scan and sort.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0081"
down_revision = "0079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_fields",
        sa.Column("provider", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "custom_fields",
        sa.Column("value_num", sa.Numeric(36, 18), nullable=True),
    )
    op.create_index(
        "ix_custom_fields_ws_key_value_num",
        "custom_fields",
        ["workspace_id", "key", "value_num"],
        postgresql_where=sa.text("value_num IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_custom_fields_ws_key_value_num", table_name="custom_fields")
    op.drop_column("custom_fields", "value_num")
    op.drop_column("custom_fields", "provider")
