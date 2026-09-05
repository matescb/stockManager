"""Units-of-measure step 2 — the internal plumbing is exact.

Migration 0074 made the quantity columns `Numeric(18, 6)`. Step 2 removed
the `int()` / `float()` coercions between the column and the caller, so a
fractional quantity now survives the whole read path instead of being
truncated on the way out of `current_quantity`.

Nothing in the public API can write a fraction yet — that is a later,
deliberately irreversible step — so every test here writes its fractional
rows through the service or the session directly. `test_uom_widening_
behaviour.py` is the companion file that pins the *unchanged* behaviour
of the integer API on top.

The failure mode being guarded against is silent: with the old
`int(sum)`, 12.5 metres read back as 12 and the half-metre was gone with
no error anywhere. So these assert on exact `Decimal` equality, and in
places on the type — `Decimal("0.3") == 0.3` is False but
`float(Decimal("0.3")) == 0.3` is True, which is exactly the confusion
that hides drift.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.domain._quantity import as_quantity, quantity_out
from app.domain.parts.models import Part
from app.domain.stock.models import StockEntry
from app.domain.stock.service import (
    available_quantity,
    bulk_current_quantities,
    bulk_current_quantities_by_lot,
    current_quantity,
    reserved_quantity,
    stock_for_storage,
    stock_summary_for_part,
    total_for_part,
)
from app.domain.storage.models import StorageLocation
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace, WorkspaceMember


@pytest.fixture
def fixtures(db: Session):
    """A workspace with one part and one storage location."""
    user = User(email=f"u-{uuid.uuid4().hex[:6]}@x.com", name="t", password_hash="x")
    db.add(user)
    db.flush()
    ws = Workspace(name="W", kind="organization", owner_user_id=user.id, currency_default="USD")
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, status="active"))
    part = Part(
        workspace_id=ws.id,
        name="Hook-up wire",
        part_type="local",
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(part)
    storage = StorageLocation(
        workspace_id=ws.id, name="Reel rack", created_by=user.id, updated_by=user.id
    )
    db.add(storage)
    db.commit()
    return ws, user, part, storage


def _ledger_row(ws, user, part, delta, *, status="on_hand", storage=None, lot_id=None):
    """A raw ledger row. Bypasses the schemas, which still reject a
    fraction — that gate is what a later step opens."""
    from app.core.time import utcnow

    return StockEntry(
        workspace_id=ws.id,
        part_id=part.id,
        lot_id=lot_id,
        storage_location_id=storage.id if storage is not None else None,
        quantity_delta=delta,
        status=status,
        operation_type="add",
        occurred_at=utcnow(),
        created_by=user.id,
    )


# ---------------------------------------------------------------------------
# The ledger round-trip
# ---------------------------------------------------------------------------


def test_fractional_quantity_survives_a_ledger_round_trip(db, fixtures):
    """12.5 m in, 12.5 m out. The old `int()` made this 12."""
    ws, user, part, _ = fixtures
    db.add(_ledger_row(ws, user, part, Decimal("12.5")))
    db.commit()

    got = current_quantity(db, workspace_id=ws.id, part_id=part.id)

    assert isinstance(got, Decimal)
    assert got == Decimal("12.500000")


def test_current_quantity_sums_fractions_exactly(db, fixtures):
    """0.1 + 0.2 a thousand times is exactly 300, not 299.99999999999994.

    This is the property option (a) of the units-of-measure design was
    chosen for: `SUM()` over `NUMERIC` is exact and order-independent, so
    a balance re-summed from scratch on every read never drifts. The same
    loop in binary float lands ~3e-11 away from 300.
    """
    ws, user, part, _ = fixtures
    for _ in range(1000):
        db.add(_ledger_row(ws, user, part, Decimal("0.1")))
        db.add(_ledger_row(ws, user, part, Decimal("0.2")))
    db.commit()

    total = current_quantity(db, workspace_id=ws.id, part_id=part.id)

    assert total == Decimal("300.000000")
    # ...and the float version of the same arithmetic does not.
    naive = 0.0
    for _ in range(1000):
        naive += 0.1
        naive += 0.2
    assert naive != 300.0


def test_partial_removal_of_a_fraction_is_exact(db, fixtures):
    ws, user, part, _ = fixtures
    db.add(_ledger_row(ws, user, part, Decimal("2.4")))
    db.add(_ledger_row(ws, user, part, Decimal("-0.9")))
    db.commit()

    assert current_quantity(db, workspace_id=ws.id, part_id=part.id) == Decimal("1.5")


# ---------------------------------------------------------------------------
# Every roll-up built on the ledger sum
# ---------------------------------------------------------------------------


def test_every_roll_up_returns_an_exact_decimal(db, fixtures):
    """`CLAUDE.md`: all quantity reads go through `current_quantity` or a
    roll-up built on it. Each one must carry the fraction, or the
    invariant only holds for whichever accessor a caller happened to
    pick."""
    ws, user, part, storage = fixtures
    db.add(_ledger_row(ws, user, part, Decimal("7.25"), storage=storage))
    db.add(_ledger_row(ws, user, part, Decimal("0.75"), status="reserved"))
    db.commit()

    assert total_for_part(db, workspace_id=ws.id, part_id=part.id) == Decimal("7.25")
    assert reserved_quantity(db, workspace_id=ws.id, part_id=part.id) == Decimal("0.75")
    assert available_quantity(db, workspace_id=ws.id, part_id=part.id) == Decimal("6.5")

    bulk = bulk_current_quantities(db, workspace_id=ws.id, part_ids=[part.id])
    assert bulk[part.id] == Decimal("7.25")

    summary = stock_summary_for_part(db, workspace_id=ws.id, part_id=part.id)
    assert [row["quantity"] for row in summary] == [Decimal("7.25")]

    by_storage = stock_for_storage(
        db, workspace_id=ws.id, storage_location_id=storage.id
    )
    assert [row["quantity"] for row in by_storage] == [Decimal("7.25")]


def test_lot_roll_up_returns_an_exact_decimal(db, fixtures):
    from app.domain.lots.models import Lot

    ws, user, part, _ = fixtures
    lot = Lot(
        workspace_id=ws.id,
        part_id=part.id,
        name="Reel A",
        source_type="manual",
        purchase_quantity=Decimal("100.5"),
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(lot)
    db.flush()
    db.add(_ledger_row(ws, user, part, Decimal("100.5"), lot_id=lot.id))
    db.commit()

    by_lot = bulk_current_quantities_by_lot(db, workspace_id=ws.id)

    assert by_lot[lot.id] == Decimal("100.5")


def test_a_sub_scale_balance_is_not_reported_as_a_holding(db, fixtures):
    """A bucket that nets to zero is dropped from the breakdowns. The
    comparison is against Decimal zero, so `Decimal("0.000000")` still
    counts as empty while `Decimal("0.000001")` does not."""
    ws, user, part, storage = fixtures
    db.add(_ledger_row(ws, user, part, Decimal("0.5"), storage=storage))
    db.add(_ledger_row(ws, user, part, Decimal("-0.5"), storage=storage))
    db.commit()

    assert stock_summary_for_part(db, workspace_id=ws.id, part_id=part.id) == []
    assert stock_for_storage(db, workspace_id=ws.id, storage_location_id=storage.id) == []

    db.add(_ledger_row(ws, user, part, Decimal("0.000001"), storage=storage))
    db.commit()

    rows = stock_summary_for_part(db, workspace_id=ws.id, part_id=part.id)
    assert [row["quantity"] for row in rows] == [Decimal("0.000001")]


def test_roll_ups_stay_workspace_scoped_for_fractional_rows(db, fixtures):
    """The widened column changes no query path, but these are the reads
    workspace isolation is load-bearing for — re-pin rather than assume
    (`CLAUDE.md`: isolation is enforced in code, not the DB)."""
    ws, user, part, _ = fixtures
    other_user = User(email=f"o-{uuid.uuid4().hex[:6]}@x.com", name="o", password_hash="x")
    db.add(other_user)
    db.flush()
    other_ws = Workspace(
        name="B", kind="organization", owner_user_id=other_user.id, currency_default="USD"
    )
    db.add(other_ws)
    db.flush()
    db.add(_ledger_row(ws, user, part, Decimal("3.5")))
    db.commit()

    assert current_quantity(db, workspace_id=other_ws.id, part_id=part.id) == Decimal(0)
    assert bulk_current_quantities(db, workspace_id=other_ws.id, part_ids=[part.id]) == {}
    assert (
        stock_summary_for_part(db, workspace_id=other_ws.id, part_id=part.id) == []
    )


# ---------------------------------------------------------------------------
# The wire boundary
# ---------------------------------------------------------------------------


def test_quantity_out_keeps_whole_values_as_json_integers():
    """Every value reachable through the API today is whole, so the wire
    format has to be byte-identical to what the integer columns emitted."""
    assert quantity_out(Decimal("5.000000")) == 5
    assert isinstance(quantity_out(Decimal("5.000000")), int)
    assert isinstance(quantity_out(Decimal("-4")), int)
    assert quantity_out(0) == 0
    assert quantity_out(None) is None


def test_quantity_out_surfaces_a_fraction_rather_than_truncating_it():
    """The loud failure mode: if a fraction ever reaches an untyped
    serialiser before the API is ready for it, it shows up in the
    response instead of being quietly rounded off."""
    assert quantity_out(Decimal("12.5")) == 12.5
    assert quantity_out(Decimal("0.000001")) == 1e-06


def test_a_quantity_multiplier_leaves_money_at_moneys_own_scale(db, fixtures):
    """The storage scale is padding, not part of the value — and it is not
    inert. `Decimal` multiplication adds exponents, so a ten that still
    carried the column's six decimal places would turn a `0.500000` unit
    price into a `5.000000000000` extended cost, which the money schemas
    render verbatim as a string. This is a real regression this PR hit.
    """
    ws, user, part, _ = fixtures
    db.add(_ledger_row(ws, user, part, Decimal("10")))
    db.commit()

    qty = current_quantity(db, workspace_id=ws.id, part_id=part.id)

    assert qty == 10
    assert str(qty) == "10"
    assert str(Decimal("0.500000") * qty) == "5.000000"
    assert str(Decimal("0.75") * qty) == "7.50"


def test_trimming_the_storage_scale_keeps_the_value(db, fixtures):
    """Trimming is cosmetic on the *value* and load-bearing on the
    *scale*: a genuine fraction keeps every digit it has."""
    ws, user, part, _ = fixtures
    db.add(_ledger_row(ws, user, part, Decimal("0.000001")))
    db.commit()

    qty = current_quantity(db, workspace_id=ws.id, part_id=part.id)

    assert qty == Decimal("0.000001")
    assert str(qty) == "0.000001"


def test_as_quantity_refuses_a_float():
    """A quantity that has already been through binary floating point has
    already lost its exactness; accepting it here would launder the loss."""
    assert as_quantity(None) == Decimal(0)
    assert as_quantity(7) == Decimal(7)
    assert as_quantity(Decimal("1.5")) == Decimal("1.5")
    # ...and it never returns scientific notation for a whole number,
    # which is what a bare `Decimal.normalize()` would do to a ten.
    assert str(as_quantity(Decimal("10.000000"))) == "10"
    assert str(as_quantity(Decimal("0.000000"))) == "0"
    with pytest.raises(TypeError):
        as_quantity(1.5)  # type: ignore[arg-type]
