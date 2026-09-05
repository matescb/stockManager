"""Units of measure, step 1: widen quantity columns to Numeric(18,6) and
add the unit columns.

Revision ID: 0074
Revises: 0073
Create Date: 2026-09-05

This is a **widening only**. Nothing in the application accepts, computes
or emits a fractional quantity after this migration — the Pydantic
schemas, the MCP tool annotations and the frontend Zod gates all still
say `int`. The point of shipping the scary part on its own is that
`integer -> numeric` is lossless and therefore *reversible* right up
until the first fractional value is written, and that write is gated
behind a later PR in this track. See `downgrade()` below.

This is substantially migration 0032 in reverse. 0032 (DB-005 / issue
#96) narrowed `project_entries.quantity` from Numeric(18,6) to Integer on
the grounds that "electronics domain — no fractional quantities are
needed". Measured stock (wire on a reel, solder paste by mass, sheet by
area) is the case that assumption missed. 0032's upgrade guard is the
model for this migration's downgrade guard.

Upgrade:
  1. `parts.unit_of_measure` and `stock_entries.unit`, both
     `NOT NULL DEFAULT 'pcs'`. ADD COLUMN with a constant default is
     metadata-only in PG 11+, so these are free — no backfill pass, no
     rewrite.
  2. Widen seven quantity columns Integer -> Numeric(18,6).

Why the unit is stamped on every ledger row and not just on the part:
storing it only on `parts` means editing a part retroactively
reinterprets every historical row — flip `pcs` to `m` and 500 pieces
silently become 500 metres, with no audit trail and no way to recover
which reading was intended. An append-only ledger exists precisely so a
written row stays a permanent, self-describing fact. The service-side
"stamp the part's unit onto the row" write, the unit-match trigger and
the part-unit immutability rule are the next step of this track; this
migration only creates the column those depend on, and every row it
creates or defaults carries the same `'pcs'` the parts do.

The scale (18, 6) matches the money columns already in the schema
(`stock_entries.unit_price`, `lots.purchase_unit_cost`), so quantity x
price stays scale-consistent, and gives micrometre resolution on metres.

`builds.quantity` deliberately stays `Integer` — you build 5 boards, not
5.5. `order_entries.order_index` / `project_entries.order_index` are
positions, not quantities, and stay `Integer` too.

**Deploy note — this rewrites `stock_entries`.** `integer -> numeric` is
not binary-coercible in Postgres, so each `ALTER COLUMN ... TYPE` holds
`ACCESS EXCLUSIVE` for a full heap rewrite. No index or constraint
references any of these columns (only `ck_project_entries_quantity_nonneg`
and the two `ck_order_entries_qty_*_nonneg` CHECKs, which Postgres
revalidates in place), so the cost is the heap rewrite alone — but that
is still O(table size), and a pending ACCESS EXCLUSIVE request blocks
every *subsequent* reader even before it is granted. Hence the
`lock_timeout` below: fail fast and let the container restart retry
rather than queue up behind a long-running transaction and stall the
whole application. Run it behind `a2enconf parts-maintenance`, after a
`pg_dump`, with the three `backend-cron*` sidecars stopped — they hold
their own connections and are not recreated by `docker compose up -d`.

Chain: 0071 (print_jobs) -> 0072 (attrition) -> 0073 (object codes) -> 0074 (this).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


#: (table, column) pairs widened by this migration, in the order the
#: rewrites run. `downgrade()` walks the same list in reverse.
_WIDENED: tuple[tuple[str, str], ...] = (
    ("stock_entries", "quantity_delta"),
    ("project_entries", "quantity"),
    ("order_entries", "quantity_ordered"),
    ("order_entries", "quantity_received"),
    ("lots", "purchase_quantity"),
    ("parts", "low_stock_report_quantity"),
    ("parts", "attrition_min_quantity"),
)

#: Columns that are NOT NULL and so must round-trip as NOT NULL.
_NOT_NULL = {
    ("stock_entries", "quantity_delta"),
    ("project_entries", "quantity"),
    ("order_entries", "quantity_ordered"),
    ("order_entries", "quantity_received"),
    ("parts", "attrition_min_quantity"),
}

#: (table, column) pairs added by this migration.
_UNIT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("parts", "unit_of_measure"),
    ("stock_entries", "unit"),
)

_DEFAULT_UNIT = "pcs"
_UNIT_LENGTH = 16

# int4 range. A Numeric(18,6) column can hold values no int4 can, so the
# downgrade guard checks range as well as fractionality.
_INT32_MIN = -2147483648
_INT32_MAX = 2147483647


def upgrade() -> None:
    # Fail fast instead of queueing behind a long transaction: a pending
    # ACCESS EXCLUSIVE request blocks every reader that arrives after it.
    # SET LOCAL so this stays scoped to the migration's own transaction.
    op.execute("SET LOCAL lock_timeout = '5s'")
    # ...but do not let a statement_timeout inherited from the role kill
    # the rewrite itself once the lock IS held.
    op.execute("SET LOCAL statement_timeout = 0")

    # 1. Unit columns. Constant DEFAULT + NOT NULL is metadata-only in
    #    PG 11+ — no table rewrite, no backfill pass.
    op.add_column(
        "parts",
        sa.Column(
            "unit_of_measure",
            sa.String(_UNIT_LENGTH),
            nullable=False,
            server_default=_DEFAULT_UNIT,
        ),
    )
    op.add_column(
        "stock_entries",
        sa.Column(
            "unit",
            sa.String(_UNIT_LENGTH),
            nullable=False,
            server_default=_DEFAULT_UNIT,
        ),
    )

    # 2. The rewrites.
    for table, column in _WIDENED:
        op.alter_column(
            table,
            column,
            type_=sa.Numeric(18, 6),
            postgresql_using=f"{column}::numeric(18,6)",
            existing_nullable=(table, column) not in _NOT_NULL,
        )

    # `alembic/env.py` wraps the whole `upgrade head` run in ONE
    # transaction, so a `SET LOCAL` here would otherwise stay in force for
    # every migration that follows this one in the same run.
    op.execute("SET LOCAL lock_timeout = DEFAULT")
    op.execute("SET LOCAL statement_timeout = DEFAULT")


def downgrade() -> None:
    """Narrow back to Integer — but **refuse** rather than truncate.

    Postgres' `numeric -> integer` cast *rounds* (2.5 becomes 2, 2.6
    becomes 3). A downgrade run against real measured stock would
    therefore not merely lose the fractional part, it would silently
    desynchronise the ledger's sums from the `lots.purchase_quantity`
    snapshots that produced them, and there would be nothing in the
    database afterwards to say it had happened. So this mirrors 0032's
    upgrade guard: count the rows that cannot survive the narrowing and
    raise if there are any.

    The consequence is deliberate: this migration is reversible on data
    that is still whole (i.e. everything, until the later PR in this
    track opens fractional input) and refuses on data that is not. That
    is the honest semantic, and it is what makes shipping the table
    rewrite early a safe move rather than a one-way door.
    """
    conn = op.get_bind()

    for table, column in _WIDENED:
        fractional = conn.execute(
            sa.text(
                # Table/column names come from the module-level literal
                # tuples above, never from input.
                f"SELECT COUNT(*) FROM {table} "
                f"WHERE {column} IS NOT NULL AND {column} <> trunc({column})"
            )
        ).scalar()
        if fractional:
            raise RuntimeError(
                f"Cannot downgrade 0074: {fractional} {table}.{column} row(s) hold "
                "fractional quantities. Narrowing to Integer would ROUND them "
                "(Postgres' numeric->integer cast rounds, it does not truncate), "
                "silently destroying measured stock and desynchronising the ledger "
                "sums from the lot snapshots. Resolve or zero those rows manually "
                "before downgrading."
            )

        out_of_range = conn.execute(
            sa.text(
                # Table/column names come from the module-level literal
                # tuples above, never from input.
                f"SELECT COUNT(*) FROM {table} "
                f"WHERE {column} IS NOT NULL "
                f"AND ({column} > {_INT32_MAX} OR {column} < {_INT32_MIN})"
            )
        ).scalar()
        if out_of_range:
            raise RuntimeError(
                f"Cannot downgrade 0074: {out_of_range} {table}.{column} row(s) fall "
                "outside the int4 range. Narrowing to Integer would overflow. "
                "Resolve those rows manually before downgrading."
            )

    for table, column in reversed(_WIDENED):
        op.alter_column(
            table,
            column,
            type_=sa.Integer(),
            postgresql_using=f"{column}::integer",
            existing_nullable=(table, column) not in _NOT_NULL,
        )

    for table, column in reversed(_UNIT_COLUMNS):
        op.drop_column(table, column)
