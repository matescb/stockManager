"""Migration 0074 — the quantity widening, and the downgrade that refuses.

0074 is the first step of the units-of-measure track and the only one
that rewrites a table. Its whole safety argument is *"reversible until
the first fractional row exists"*, so the two things worth pinning are:

  1. it round-trips — upgrade, downgrade, upgrade again — leaving the
     seven quantity columns back at `numeric(18,6)` and the two unit
     columns back with their `'pcs'` default;
  2. it **refuses** to downgrade once a fractional row exists, rather
     than narrowing and letting Postgres' `numeric -> integer` cast
     round the value away. That cast rounds (2.5 -> 2, 2.6 -> 3), so a
     permissive downgrade would silently destroy measured stock *and*
     desynchronise the ledger sums from the `lots.purchase_quantity`
     snapshots, with nothing left in the database to say it happened.

Test (2) is the one that makes the risk non-silent, and it is the mirror
image of the guard 0032 put on its own *upgrade*
(`alembic/versions/0032_integer_quantities.py:44-51`).

`real_db` because the migration runs on its own connection and has to
see committed rows; the marker also resets the schema around the test so
a downgrade can't leak into anything else. `slow` because each test
walks the alembic chain — these run in CI's dedicated `-m slow` step,
alongside `test_migrations.py`.

The parent revision is read out of the script directory rather than
hard-coded, so this file keeps working when 0074 is rebased onto a
different predecessor.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import text

from alembic import command
from app.core.config import settings
from app.main import app
from tests._factories import add_stock, create_part, signup_user

pytestmark = [pytest.mark.real_db, pytest.mark.slow]

_BACKEND_ROOT = Path(__file__).resolve().parents[1]

REVISION = "0074"

#: Every (table, column) 0074 widens, mirroring `_WIDENED` in the migration.
WIDENED = (
    ("stock_entries", "quantity_delta"),
    ("project_entries", "quantity"),
    ("order_entries", "quantity_ordered"),
    ("order_entries", "quantity_received"),
    ("lots", "purchase_quantity"),
    ("parts", "low_stock_report_quantity"),
    ("parts", "attrition_min_quantity"),
)

#: Every (table, column) 0074 adds.
UNIT_COLUMNS = (("parts", "unit_of_measure"), ("stock_entries", "unit"))


def _alembic_cfg() -> AlembicConfig:
    cfg = AlembicConfig(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings().DATABASE_URL)
    return cfg


def _parent_revision(cfg: AlembicConfig) -> str:
    """0074's `down_revision`, read from the chain rather than hard-coded."""
    down = ScriptDirectory.from_config(cfg).get_revision(REVISION).down_revision
    assert isinstance(down, str) and down, f"{REVISION} must have a single parent"
    return down


def _column_type(db, table: str, column: str) -> tuple[str, int | None, int | None]:
    row = db.execute(
        text(
            "SELECT data_type, numeric_precision, numeric_scale"
            " FROM information_schema.columns"
            " WHERE table_schema = 'public'"
            "   AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).one_or_none()
    return (None, None, None) if row is None else (row[0], row[1], row[2])


def _assert_widened(db) -> None:
    for table, column in WIDENED:
        assert _column_type(db, table, column) == ("numeric", 18, 6), (
            f"{table}.{column} should be numeric(18,6) at {REVISION}"
        )
    for table, column in UNIT_COLUMNS:
        data_type, _, _ = _column_type(db, table, column)
        assert data_type == "character varying", f"{table}.{column} missing at {REVISION}"
        default = db.execute(
            text(
                "SELECT column_default FROM information_schema.columns"
                " WHERE table_schema = 'public'"
                "   AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).scalar_one()
        assert default is not None and "pcs" in default, (
            f"{table}.{column} should default to 'pcs', got {default!r}"
        )
        nullable = db.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns"
                " WHERE table_schema = 'public'"
                "   AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).scalar_one()
        assert nullable == "NO", f"{table}.{column} should be NOT NULL"


def _assert_narrowed(db) -> None:
    for table, column in WIDENED:
        data_type, _, _ = _column_type(db, table, column)
        assert data_type == "integer", (
            f"{table}.{column} should be integer below {REVISION}, got {data_type}"
        )
    for table, column in UNIT_COLUMNS:
        data_type, _, _ = _column_type(db, table, column)
        assert data_type is None, f"{table}.{column} should not exist below {REVISION}"


def test_0074_upgrade_downgrade_upgrade_round_trip(db):
    """The full sweep on a clean schema: the chain is walkable in both
    directions and the second upgrade reproduces the first."""
    cfg = _alembic_cfg()
    parent = _parent_revision(cfg)

    # conftest migrated to head, so we start widened.
    _assert_widened(db)

    db.commit()
    command.downgrade(cfg, parent)
    _assert_narrowed(db)

    command.upgrade(cfg, REVISION)
    _assert_widened(db)


def test_0074_downgrade_refuses_when_a_ledger_row_is_fractional(db):
    """The guard that makes the irreversible moment loud.

    A forced narrowing would ROUND 2.5 to 2 — losing measured stock and
    leaving the ledger disagreeing with the lot snapshot that produced
    it. `downgrade()` must raise instead, and must leave the schema and
    the data exactly as it found them.
    """
    cfg = _alembic_cfg()
    parent = _parent_revision(cfg)

    client = TestClient(app)
    signup_user(client)
    part_id = create_part(client, "Hookup wire")
    add_stock(client, part_id, 10)
    db.commit()

    # Reach past the (still integer-only) API to write what a later step
    # of this track will make reachable through it.
    db.execute(
        text("UPDATE stock_entries SET quantity_delta = 2.5 WHERE part_id = :p"),
        {"p": part_id},
    )
    db.commit()

    with pytest.raises(RuntimeError) as excinfo:
        command.downgrade(cfg, parent)

    message = str(excinfo.value)
    assert "stock_entries.quantity_delta" in message
    assert "fractional" in message

    # Nothing was narrowed and nothing was rounded.
    _assert_widened(db)
    assert db.execute(
        text("SELECT quantity_delta FROM stock_entries WHERE part_id = :p"),
        {"p": part_id},
    ).scalar_one() == pytest.approx(2.5)
    db.commit()


def test_0074_downgrade_refuses_for_every_widened_column(db):
    """The guard covers all seven columns, not just the ledger.

    `parts.low_stock_report_quantity` is the cheapest of the other six to
    make fractional; if the loop only checked `stock_entries` this would
    narrow happily and round 1.5 to 2.
    """
    cfg = _alembic_cfg()
    parent = _parent_revision(cfg)

    client = TestClient(app)
    signup_user(client)
    part_id = create_part(client, "Solder paste")
    db.commit()

    db.execute(
        text("UPDATE parts SET low_stock_report_quantity = 1.5 WHERE id = :p"),
        {"p": part_id},
    )
    db.commit()

    with pytest.raises(RuntimeError, match="parts.low_stock_report_quantity"):
        command.downgrade(cfg, parent)

    _assert_widened(db)


def test_0074_downgrade_refuses_to_drop_a_non_default_unit_stamp(db):
    """A whole number is only safe to narrow if it still means *pieces*.

    `downgrade()` drops the unit columns, so a row stamped `'m'` would
    come out of it as an ambiguous `12` with nothing left to say it was
    metres — the exact loss the per-row stamp exists to prevent. The
    fractional guard alone would not catch it, because 12 is whole.

    The stamped row is arranged the way the application will produce one
    once units are selectable: set the *part's* unit first, then add
    stock, which copies it onto the ledger row. Rewriting `unit` after the
    fact is no longer possible — alembic 0077 made the ledger's stamp
    immutable, which is the same append-only property this test defends.

    A consequence of 0077 worth naming: the part's unit and its rows'
    stamps can no longer diverge, so a non-default ledger stamp always
    implies a non-default `parts.unit_of_measure`. Both are in the guard's
    `_UNIT_COLUMNS` loop and either alone is enough to refuse, so this
    asserts on the guard's *reason* rather than on whichever column the
    loop happens to reach first.
    """
    cfg = _alembic_cfg()
    parent = _parent_revision(cfg)

    client = TestClient(app)
    signup_user(client)
    part_id = create_part(client, "Spool")
    db.execute(
        text("UPDATE parts SET unit_of_measure = 'm' WHERE id = :p"),
        {"p": part_id},
    )
    db.commit()

    add_stock(client, part_id, 12)
    db.commit()
    assert db.execute(
        text("SELECT unit FROM stock_entries WHERE part_id = :p"),
        {"p": part_id},
    ).scalar_one() == "m", "uom step 3 should have stamped the part's unit"
    # Release the ACCESS SHARE this read just took. `downgrade()` walks
    # back through 0077, which needs ACCESS EXCLUSIVE on `stock_entries`
    # to drop its triggers, and 0074's own guard LOCKs the table outright
    # — either would queue behind an open read transaction held by this
    # very test and hang it.
    db.commit()

    with pytest.raises(RuntimeError, match="carry a unit other than 'pcs'"):
        command.downgrade(cfg, parent)

    _assert_widened(db)
    assert db.execute(
        text("SELECT unit FROM stock_entries WHERE part_id = :p"),
        {"p": part_id},
    ).scalar_one() == "m"
    assert db.execute(
        text("SELECT unit_of_measure FROM parts WHERE id = :p"),
        {"p": part_id},
    ).scalar_one() == "m"
    db.commit()


def test_0074_downgrade_succeeds_once_the_fractional_row_is_resolved(db):
    """Reversibility is a property of the data, not a one-way door.

    Zero the fractional row out and the same downgrade that just refused
    goes through — which is the whole reason this migration can ship
    ahead of the step that opens fractional input.
    """
    cfg = _alembic_cfg()
    parent = _parent_revision(cfg)

    client = TestClient(app)
    signup_user(client)
    part_id = create_part(client, "Enamelled wire")
    add_stock(client, part_id, 10)
    db.commit()

    db.execute(
        text("UPDATE stock_entries SET quantity_delta = 2.5 WHERE part_id = :p"),
        {"p": part_id},
    )
    db.commit()
    with pytest.raises(RuntimeError):
        command.downgrade(cfg, parent)

    db.execute(
        text("UPDATE stock_entries SET quantity_delta = 3 WHERE part_id = :p"),
        {"p": part_id},
    )
    db.commit()

    command.downgrade(cfg, parent)
    _assert_narrowed(db)
    assert db.execute(
        text("SELECT quantity_delta FROM stock_entries WHERE part_id = :p"),
        {"p": part_id},
    ).scalar_one() == 3

    # That SELECT holds ACCESS SHARE on stock_entries until the session's
    # transaction ends, and the re-upgrade needs ACCESS EXCLUSIVE. Release
    # it, or 0074's own `lock_timeout` (correctly) cancels the ALTER — the
    # exact lock-queue hazard the deploy note in the migration warns about.
    db.commit()

    command.upgrade(cfg, REVISION)
    _assert_widened(db)
