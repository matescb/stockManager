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

**Deploy note — this rewrites five tables.** `integer -> numeric` is not
binary-coercible in Postgres, so each `ALTER TABLE ... ALTER COLUMN ...
TYPE` holds `ACCESS EXCLUSIVE` while it rewrites the heap **and rebuilds
every index on that table**, whether or not the index mentions the
changed column. For `parts` that means the five `pg_trgm` GIN indexes get
rebuilt too. Nothing *blocks* the change — no view, generated column, FK
or index references any widened column, the two plpgsql triggers
(`check_stock_nonneg` from 0013, `check_stock_entries_workspace_fks` from
0050) are not dependency-tracked and neither needs editing, and the three
`ck_*_nonneg` CHECKs from 0032 are revalidated in place — but the cost is
real: budget roughly **2x the total relation size (heap + indexes) in
free disk and a comparable volume of WAL**, since every old relfilenode
is held until the single commit at the end of the run.

Because the cost is per *table*, the widenings are grouped into one
`ALTER TABLE` per table (`parts` and `order_entries` each take two
columns), which is five rewrites rather than seven.

A pending `ACCESS EXCLUSIVE` request blocks every *subsequent* reader
even before it is granted, hence the `lock_timeout` below: fail fast
rather than queue behind a long-running transaction and stall the whole
application. Note the trade-off — a timeout aborts the entire
`alembic upgrade head`, the container's `sh -c "alembic ... && exec
uvicorn"` exits, and `restart: unless-stopped` retries. That is a
crash-loop, not a graceful degradation, if something is *persistently*
holding a conflicting lock.

So, before approving the `production` environment gate:

  1. confirm the `predeploy-dump.sh` artifact landed — it is the only
     rollback for the two new columns (see `downgrade()`);
  2. size it: `SELECT relname, pg_size_pretty(pg_total_relation_size(oid))
     FROM pg_class WHERE relname IN ('stock_entries', 'parts', 'lots',
     'order_entries', 'project_entries');` and check free disk is >= 2x
     that sum;
  3. `docker compose -f docker-compose.prod.yml stop backend-cron
     backend-cron-alerts backend-cron-sessions`, and start them again
     after the health gate passes. They declare `depends_on: backend:
     condition: service_healthy`, so under `docker compose up -d --build`
     the *old* cron containers keep their connections open while the new
     backend runs the migration — a `sourcing-cache-sweep` transaction
     touching `parts` is exactly what turns the `lock_timeout` above into
     a crash-loop. The deploy job's own health gate is 30 x 5s, so a
     rewrite longer than ~150s also fails the job (maintenance mode is
     lifted by its `trap ... EXIT`, though the vhost still maps 502/503/504
     to the maintenance page).

Chain: 0071 (print_jobs) -> 0072 (attrition) -> 0073 (object codes) -> 0074 (this).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


#: Columns widened by this migration, grouped by table so each table is
#: rewritten exactly once. A rewrite reindexes the whole table, so two
#: separate ALTERs against `parts` would rebuild its five pg_trgm GIN
#: indexes twice for no reason. `downgrade()` walks this list in reverse.
_WIDENED_BY_TABLE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("stock_entries", ("quantity_delta",)),
    ("project_entries", ("quantity",)),
    ("order_entries", ("quantity_ordered", "quantity_received")),
    ("lots", ("purchase_quantity",)),
    ("parts", ("low_stock_report_quantity", "attrition_min_quantity")),
)

#: Flattened `(table, column)` view of the above, for the downgrade guards.
_WIDENED: tuple[tuple[str, str], ...] = tuple(
    (table, column) for table, columns in _WIDENED_BY_TABLE for column in columns
)

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


def _alter_clauses(columns: tuple[str, ...], pg_type: str) -> str:
    """`ALTER COLUMN a TYPE t USING a::t, ALTER COLUMN b TYPE t USING b::t`.

    Column names come from the module-level literal tuples above, never
    from input.
    """
    return ", ".join(
        f"ALTER COLUMN {column} TYPE {pg_type} USING {column}::{pg_type}"
        for column in columns
    )


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

    # 2. The rewrites — one ALTER TABLE per table (see _WIDENED_BY_TABLE).
    #    Raw SQL rather than op.alter_column() because alembic emits one
    #    statement per column, and each statement is a separate full-table
    #    rewrite + reindex.
    for table, columns in _WIDENED_BY_TABLE:
        op.execute(f"ALTER TABLE {table} {_alter_clauses(columns, 'numeric(18,6)')}")

    # `alembic/env.py` wraps the whole `upgrade head` run in ONE
    # transaction, so a `SET LOCAL` here would otherwise stay in force for
    # every migration that follows this one in the same run. `= DEFAULT`
    # resets to the role / postgresql.conf value (both `0` on the stock
    # postgres image), not to whatever the session had set.
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

    The same argument applies to the unit columns, which this drops: a
    whole-number row is only safe to narrow if it also still means
    *pieces*. So the guard covers non-default unit stamps too — dropping
    the stamp off a 12 m spool is the "500 pieces become 500 metres" loss
    the column exists to prevent.
    """
    conn = op.get_bind()

    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = 0")

    # Take the exclusive lock BEFORE counting. A bare COUNT(*) holds only
    # ACCESS SHARE, so without this a fractional row inserted between the
    # guard and the ALTER would sail past the check and be rounded away.
    # Deterministic order, so two concurrent runs cannot deadlock.
    for table, _ in _WIDENED_BY_TABLE:
        op.execute(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE")

    for table, column in _UNIT_COLUMNS:
        stamped = conn.execute(
            sa.text(
                # Names are module-level literals, never input.
                f"SELECT COUNT(*) FROM {table} WHERE {column} <> :unit"  # noqa: E501
            ),
            {"unit": _DEFAULT_UNIT},
        ).scalar()
        if stamped:
            raise RuntimeError(
                f"Cannot downgrade 0074: {stamped} {table}.{column} row(s) carry a "
                f"unit other than '{_DEFAULT_UNIT}'. Dropping the column would "
                "erase what those quantities measure — a 12 m spool would become "
                "an ambiguous '12' with nothing left to say it was metres. "
                "Convert those rows back before downgrading."
            )

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

    # One ALTER TABLE per table, same grouping as upgrade().
    for table, columns in reversed(_WIDENED_BY_TABLE):
        op.execute(f"ALTER TABLE {table} {_alter_clauses(columns, 'integer')}")

    for table, column in reversed(_UNIT_COLUMNS):
        op.drop_column(table, column)
