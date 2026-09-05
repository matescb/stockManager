"""Unit stamping and its three backstops (units-of-measure step 3).

Migration 0074 gave every part a `unit_of_measure` and every ledger row a
`unit`, both defaulting to `'pcs'`, and deferred the "write the part's unit
onto the row" half. That half is `stock/service.py::unit_for_part`, called
from all eleven allow-listed `StockEntry(...)` constructions; alembic 0077
adds the triggers that keep the stamp honest.

What these tests pin:

* **Nothing observable changed.** Every part is `'pcs'` today, so the stamp
  writes exactly the value the column's server default already produced.
  `test_every_writer_path_stamps_pcs_today` walks every writer the app has
  — add / remove / move / split-lot move / adjust / order receive / build
  reserve, consume, produce and release / kit move / scan import — and
  asserts the ledger is byte-identical to the pre-stamp behaviour.
* **The stamp always agrees with the part**, which is the invariant
  `current_quantity`'s `SUM(quantity_delta)` silently depends on: summing
  5 pcs with 3 m yields a number that means nothing.
* **The triggers are backstops, not the control.** They can only fire for
  raw SQL, so every test that provokes one goes around the service layer
  on purpose.
* **The part-unit-change decision** (frozen once the part has any ledger
  row — see the 0077 docstring), including the case the looser "net
  balance is zero" rule would have wrongly allowed.
* **No cross-workspace oracle.** The unit-match trigger reads `parts`, so
  it gets the same isolation treatment as anything else that queries.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from app.domain._quantity import DEFAULT_UNIT
from app.domain.parts.models import Part
from app.domain.stock.models import StockEntry
from app.infra.db import SessionLocal
from app.main import app
from tests._factories import add_stock as _add_stock
from tests._factories import create_part as _create_part
from tests._factories import create_storage as _create_storage
from tests._factories import signup_user

_TRIGGER_ERRORS = (IntegrityError, ProgrammingError, DBAPIError)


@pytest.fixture
def authed() -> TestClient:
    c = TestClient(app)
    signup_user(c)
    return c


def _signup(c: TestClient, email: str) -> str:
    return signup_user(c, email=email).json()["data"]["workspace_id"]


def _rows_for_part(part_id: str) -> list[StockEntry]:
    with SessionLocal() as s:
        return list(
            s.execute(
                select(StockEntry)
                .where(StockEntry.part_id == uuid.UUID(part_id))
                .order_by(StockEntry.occurred_at, StockEntry.id)
            ).scalars()
        )


def _all_rows() -> list[StockEntry]:
    with SessionLocal() as s:
        return list(s.execute(select(StockEntry)).scalars())


def _part_unit(part_id: str) -> str:
    with SessionLocal() as s:
        return s.execute(
            select(Part.unit_of_measure).where(Part.id == uuid.UUID(part_id))
        ).scalar_one()


def _force_part_unit(part_id: str, unit: str) -> None:
    """Set a part's unit directly.

    There is no API for this yet — `PartIn` / `PartPatch` carry no such
    field — so tests that need a non-default unit go through SQL, which is
    also the only path the `parts_unit_of_measure_change_check` trigger can
    ever see.
    """
    with SessionLocal() as s:
        s.execute(
            text("UPDATE parts SET unit_of_measure = :u WHERE id = :id"),
            {"u": unit, "id": part_id},
        )
        s.commit()


def _raw_insert(
    *,
    workspace_id: str,
    part_id: str,
    unit: str,
    quantity: int = 1,
) -> None:
    """Insert a ledger row straight through SQL, bypassing the service.

    This is the only way to reach the trigger: `unit_for_part` makes a
    service-written row match its part by construction.
    """
    with SessionLocal() as s:
        s.execute(
            text(
                "INSERT INTO stock_entries ("
                "id, workspace_id, part_id, quantity_delta, unit, status, "
                "operation_type, occurred_at, created_at"
                ") VALUES ("
                ":id, :workspace_id, :part_id, :quantity, :unit, 'on_hand', "
                "'add', now(), now()"
                ")"
            ),
            {
                "id": str(uuid.uuid4()),
                "workspace_id": workspace_id,
                "part_id": part_id,
                "quantity": quantity,
                "unit": unit,
            },
        )
        s.commit()


# ---------------------------------------------------------------------------
# The regression that matters: today's behaviour is unchanged.
# ---------------------------------------------------------------------------


def _project(c: TestClient, name: str) -> str:
    r = c.post("/api/projects", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _add_entry(c: TestClient, project_id: str, **body) -> dict:
    r = c.post(f"/api/projects/{project_id}/entries", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]


def _build(c: TestClient, project_id: str, quantity: int, name: str = "B") -> str:
    r = c.post(
        "/api/builds",
        json={"name": name, "project_id": project_id, "quantity": quantity},
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def test_every_writer_path_stamps_pcs_today(authed: TestClient) -> None:
    """Exercise every ledger writer the app has; assert the ledger is
    identical to what the column default produced before the stamp existed.

    This is the constraint the whole step is held to: `parts.unit_of_measure`
    is `'pcs'` for every part in existence, so stamping from the part must
    be a no-op on real data. If this test ever fails, the stamp has started
    changing history rather than recording it.

    The `operation_type` coverage assertion at the end is what stops this
    test rotting. It compares the emitted verbs for *equality*, not
    containment, so it fails in both directions: a verb that stops being
    written (this workload drifted) and a verb that starts (a new writer
    landed and its rows are now in the `all(... == DEFAULT_UNIT)` check
    above whether the author remembered this file or not). A genuinely new
    endpoint still has to be added to the workload by hand — the failure
    is the prompt to do it.
    """
    c = authed
    part = _create_part(c, "R10k 0402")
    sub_part = _create_part(c, "Sub-assembly")
    bin_a = _create_storage(c, "Bin A")
    bin_b = _create_storage(c, "Bin B")
    tray = _create_storage(c, "Kitting tray")

    # add / remove / adjust
    _add_stock(c, part, 500, bin_a)
    assert c.post(
        "/api/stock/remove",
        json={"part_id": part, "quantity": 5, "storage_location_id": bin_a},
    ).status_code == 200
    assert c.post(
        "/api/stock/adjust",
        json={
            "part_id": part,
            "actual_quantity": 480,
            "storage_location_id": bin_a,
        },
    ).status_code == 200

    # move (plain) and move (split lot)
    assert c.post(
        "/api/stock/move",
        json={
            "part_id": part,
            "quantity": 20,
            "source_storage_location_id": bin_a,
            "destination_storage_location_id": bin_b,
        },
    ).status_code == 200
    lot_add = _add_stock(c, part, 40, bin_a, lot_name="Reel-1").json()["data"]
    assert c.post(
        "/api/stock/move",
        json={
            "part_id": part,
            "quantity": 10,
            "source_storage_location_id": bin_a,
            "source_lot_id": lot_add["lot_id"],
            "destination_storage_location_id": bin_b,
            "split_lot": True,
        },
    ).status_code == 200

    # order receive
    r = c.post("/api/orders", json={"name": "PO-1", "currency": "USD"})
    assert r.status_code in (200, 201), r.text
    order_id = r.json()["data"]["id"]
    r = c.post(
        f"/api/orders/{order_id}/entries",
        json={"part_id": part, "quantity_ordered": 25},
    )
    assert r.status_code in (200, 201), r.text
    entry_id = r.json()["data"]["id"]
    r = c.post(
        f"/api/orders/{order_id}/receive",
        json={"lines": [{"order_entry_id": entry_id, "quantity": 25}]},
    )
    assert r.status_code == 200, r.text

    # build reserve / kit / consume / produce / release
    project_id = _project(c, "PCB-1")
    _add_entry(c, project_id, part_id=part, quantity=2)
    r = c.patch(
        f"/api/projects/{project_id}",
        json={"associated_subassembly_part_id": sub_part},
    )
    assert r.status_code == 200, r.text
    # Creating the build applies the reservations, so the `reserve` rows
    # are already written by the time this returns.
    build_id = _build(c, project_id, quantity=3)

    r = c.post(f"/api/builds/{build_id}/kit", json={"storage_location_id": tray})
    assert r.status_code == 200, r.text
    r = c.post(
        f"/api/builds/{build_id}/consume",
        json={
            "lines": [
                {
                    "project_entry_id": _bom_entry_id(c, project_id, part),
                    "part_id": part,
                    "quantity": 6,
                    "storage_location_id": tray,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text

    rows = _all_rows()
    # Sanity: the workload above really did write a broad ledger.
    assert len(rows) >= 12, f"expected a broad ledger, got {len(rows)} rows"

    assert all(row.unit == DEFAULT_UNIT for row in rows), sorted(
        {(row.operation_type, row.unit) for row in rows if row.unit != DEFAULT_UNIT}
    )

    covered = {row.operation_type for row in rows}
    expected = {
        "add",
        "remove",
        "adjust",
        "move_out",
        "move_in",
        "receive",
        "reserve",
        "release",
        "build_consume",
        "build_produce",
    }
    assert covered == expected, (
        "ledger verbs drifted — "
        f"missing: {sorted(expected - covered)}, unexpected: {sorted(covered - expected)}"
    )


def _bom_entry_id(c: TestClient, project_id: str, part_id: str) -> str:
    rows = c.get(f"/api/projects/{project_id}/entries").json()["data"]
    rows = rows["rows"] if isinstance(rows, dict) else rows
    return next(row["id"] for row in rows if row.get("part_id") == part_id)


def test_scan_import_stamps_the_parts_unit(authed: TestClient, monkeypatch) -> None:
    """The scan-import front door writes through `add_stock`, so it inherits
    the stamp. Pinned separately because it is the one writer whose quantity
    arrives from an outside bag label rather than a form, and because it
    creates the part and its first ledger row in a single request — the one
    place where "stamp from the part" and "the part was made a moment ago"
    have to line up."""
    c = authed
    # Bulk-import is a provider flow: it refuses to run without one, and
    # only the create-a-new-part branch adds stock.
    assert c.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": "fake-key"},
    ).status_code == 200
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: {
            "Errors": [],
            "SearchResults": {
                "NumberOfResult": 1,
                "Parts": [
                    {
                        "Manufacturer": "ACME",
                        "ManufacturerPartNumber": "BAG-MPN-1",
                        "Description": "Bagged part",
                        "ProductAttributes": [],
                    }
                ],
            },
        },
    )

    r = c.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{"mpn": "BAG-MPN-1", "quantity": 12, "lot_name": "BAG-1"}]},
    )
    assert r.status_code == 200, r.text
    row = r.json()["data"]["rows"][0]
    assert row["status"] == "created", r.text
    part = row["part_id"]

    rows = _rows_for_part(part)
    assert rows, "scan import wrote no ledger row"
    assert [r_.quantity_delta for r_ in rows] == [Decimal(12)]
    assert all(r_.unit == DEFAULT_UNIT for r_ in rows)
    assert all(r_.unit == _part_unit(part) for r_ in rows)


def test_stamp_follows_the_part_not_the_column_default(authed: TestClient) -> None:
    """The stamp must come from the part, not from `DEFAULT 'pcs'`.

    With every part on `'pcs'`, the two are indistinguishable — which is
    exactly why this test moves a fresh part to `'m'` first. Without it,
    "stamping works" would be satisfied by a service that stamps nothing
    and lets the server default fill in.
    """
    c = authed
    part = _create_part(c, "Hook-up wire")
    _force_part_unit(part, "m")

    _add_stock(c, part, 25)

    rows = _rows_for_part(part)
    assert [row.unit for row in rows] == ["m"]
    assert rows[0].quantity_delta == Decimal(25)


def test_moves_stamp_both_halves_from_the_part(authed: TestClient) -> None:
    """An OUT and its matching IN must carry the same unit or they would
    not cancel in `SUM(quantity_delta)`."""
    c = authed
    part = _create_part(c, "Solder wire")
    _force_part_unit(part, "m")
    src = _create_storage(c, "Spool shelf")
    dst = _create_storage(c, "Bench")
    _add_stock(c, part, 100, src)

    r = c.post(
        "/api/stock/move",
        json={
            "part_id": part,
            "quantity": 30,
            "source_storage_location_id": src,
            "destination_storage_location_id": dst,
        },
    )
    assert r.status_code == 200, r.text

    pair = [row for row in _rows_for_part(part) if row.operation_type.startswith("move_")]
    assert {row.operation_type for row in pair} == {"move_out", "move_in"}
    assert {row.unit for row in pair} == {"m"}


# ---------------------------------------------------------------------------
# Backstop 1 — the unit-match trigger.
# ---------------------------------------------------------------------------


def test_raw_insert_with_a_mismatched_unit_is_rejected(authed: TestClient) -> None:
    """The backstop doing its job: a row that claims metres against a part
    measured in pieces never lands, so one part's ledger can never hold two
    units and `SUM(quantity_delta)` stays meaningful."""
    c = authed
    ws_id = c.get("/api/workspaces/current").json()["data"]["id"]
    part = _create_part(c, "Trigger part")

    with pytest.raises(_TRIGGER_ERRORS) as excinfo:
        _raw_insert(workspace_id=ws_id, part_id=part, unit="m")

    assert "does not match parts.unit_of_measure" in str(excinfo.value)
    assert _pgcode(excinfo.value) == "23514"


def test_raw_insert_with_the_matching_unit_is_accepted(authed: TestClient) -> None:
    """The trigger must not be a blanket ban on SQL-level inserts — it
    rejects disagreement, nothing else."""
    c = authed
    ws_id = c.get("/api/workspaces/current").json()["data"]["id"]
    part = _create_part(c, "Agreeing part")
    _force_part_unit(part, "g")

    _raw_insert(workspace_id=ws_id, part_id=part, unit="g", quantity=7)

    rows = _rows_for_part(part)
    assert [row.unit for row in rows] == ["g"]


def test_hard_deleting_a_part_keeps_its_rows_and_their_stamps(
    authed: TestClient,
) -> None:
    """`stock_entries.part_id` is nullable by design — hard-deleting a part
    NULLs it and leaves the history behind (ADR-0028, `ON DELETE SET NULL`).

    Two things have to hold. The delete itself must not be blocked: neither
    the unit-match trigger (which skips a NULL `part_id`, since there is no
    part left to agree with) nor the immutability trigger (the SET NULL is
    an UPDATE of `part_id`, not of `unit`) may fire. And the orphaned rows
    must keep their stamp, which is now the *only* surviving record of what
    those quantities measured — the whole reason the stamp is per-row.
    """
    c = authed
    part = _create_part(c, "Doomed part")
    _force_part_unit(part, "m")
    _add_stock(c, part, 9)

    with SessionLocal() as s:
        s.execute(text("DELETE FROM parts WHERE id = :id"), {"id": part})
        s.commit()

    orphans = [row for row in _all_rows() if row.part_id is None]
    assert [row.unit for row in orphans] == ["m"]


# ---------------------------------------------------------------------------
# Backstop 2 — the ledger row's unit is immutable.
# ---------------------------------------------------------------------------


def test_updating_a_ledger_rows_unit_is_rejected(authed: TestClient) -> None:
    """The ledger is append-only, so a row's unit is a historical fact.

    This is the attack the per-row stamp exists to stop: without it,
    rewriting units in place would silently reinterpret every past
    quantity, and `SUM(quantity_delta)` would keep returning a number as
    if nothing had happened.
    """
    c = authed
    part = _create_part(c, "Immutable part")
    _add_stock(c, part, 3)
    row_id = _rows_for_part(part)[0].id

    with pytest.raises(_TRIGGER_ERRORS) as excinfo:
        with SessionLocal() as s:
            s.execute(
                text("UPDATE stock_entries SET unit = 'm' WHERE id = :id"),
                {"id": str(row_id)},
            )
            s.commit()

    assert "immutable" in str(excinfo.value)
    assert _pgcode(excinfo.value) == "23514"
    assert _rows_for_part(part)[0].unit == DEFAULT_UNIT


def test_updating_other_ledger_columns_still_works(authed: TestClient) -> None:
    """`BEFORE UPDATE OF unit` must not tax the common path. A move already
    back-patches `related_entry_id` after insert; if the immutability
    trigger were written as a blanket `BEFORE UPDATE`, that would be the
    first thing it broke."""
    c = authed
    part = _create_part(c, "Patchable part")
    _add_stock(c, part, 4)
    row_id = _rows_for_part(part)[0].id

    with SessionLocal() as s:
        s.execute(
            text("UPDATE stock_entries SET comments = 'edited' WHERE id = :id"),
            {"id": str(row_id)},
        )
        s.commit()

    assert _rows_for_part(part)[0].comments == "edited"


def test_rewriting_a_units_value_to_itself_is_allowed(authed: TestClient) -> None:
    """`UPDATE OF unit` fires whenever the column is in the SET list, even
    when the value is unchanged. Comparing OLD/NEW rather than trusting the
    trigger's firing condition is what keeps a full-row UPDATE working."""
    c = authed
    part = _create_part(c, "Idempotent part")
    _add_stock(c, part, 2)
    row_id = _rows_for_part(part)[0].id

    with SessionLocal() as s:
        s.execute(
            text("UPDATE stock_entries SET unit = :u WHERE id = :id"),
            {"u": DEFAULT_UNIT, "id": str(row_id)},
        )
        s.commit()

    assert _rows_for_part(part)[0].unit == DEFAULT_UNIT


# ---------------------------------------------------------------------------
# Backstop 3 — a part's unit freezes once it has ledger rows.
# ---------------------------------------------------------------------------


def test_part_unit_change_allowed_while_the_part_has_no_ledger_rows(
    authed: TestClient,
) -> None:
    """The permissive half of the decision. A freshly created part is the
    overwhelmingly common case for wanting to set a unit, and refusing
    outright would leave every part stuck on `pcs` forever."""
    c = authed
    part = _create_part(c, "Fresh part")

    _force_part_unit(part, "m")

    assert _part_unit(part) == "m"


def test_changing_a_fresh_parts_unit_does_not_wedge_future_writes(
    authed: TestClient,
) -> None:
    """The trap the rule exists to avoid: a unit change that the trigger
    then makes every subsequent ledger write fail on.

    Because the service stamps from the part, a permitted change moves the
    stamp with it and the next write agrees by construction.
    """
    c = authed
    part = _create_part(c, "Retuned part")
    _force_part_unit(part, "m")

    _add_stock(c, part, 15)
    r = c.post("/api/stock/remove", json={"part_id": part, "quantity": 5})
    assert r.status_code == 200, r.text

    assert {row.unit for row in _rows_for_part(part)} == {"m"}
    assert c.get(f"/api/parts/{part}/stock").json()["data"]["total_on_hand"] == 10


def test_part_unit_change_rejected_once_the_part_has_ledger_rows(
    authed: TestClient,
) -> None:
    c = authed
    part = _create_part(c, "Stocked part")
    _add_stock(c, part, 20)

    with pytest.raises(_TRIGGER_ERRORS) as excinfo:
        _force_part_unit(part, "m")

    assert "cannot change" in str(excinfo.value)
    assert _pgcode(excinfo.value) == "23514"
    assert _part_unit(part) == DEFAULT_UNIT


def test_part_unit_change_still_rejected_after_the_stock_is_zeroed(
    authed: TestClient,
) -> None:
    """**The decision test.** The uom design sketched a looser rule —
    allow the change when the net balance is zero. This is why it was not
    taken.

    Zeroing the stock does not remove the ledger rows; nothing does, that
    is the point of an append-only ledger. Under the looser rule this part
    would end up with `pcs` history and `m` history in one ledger, and
    every roll-up built on `current_quantity` would go on summing them
    together. "No ledger rows at all" is the only rule that keeps a part's
    ledger single-unit by construction.
    """
    c = authed
    part = _create_part(c, "Drained part")
    _add_stock(c, part, 20)
    r = c.post("/api/stock/remove", json={"part_id": part, "quantity": 20})
    assert r.status_code == 200, r.text
    assert c.get(f"/api/parts/{part}/stock").json()["data"]["total_on_hand"] == 0

    with pytest.raises(_TRIGGER_ERRORS):
        _force_part_unit(part, "m")

    assert _part_unit(part) == DEFAULT_UNIT


def test_editing_other_part_columns_still_works_with_stock_present(
    authed: TestClient,
) -> None:
    """`BEFORE UPDATE OF unit_of_measure` must not touch ordinary part
    edits — a stocked part has to stay renameable."""
    c = authed
    part = _create_part(c, "Renamable part")
    _add_stock(c, part, 5)

    r = c.patch(f"/api/parts/{part}", json={"name": "Renamed part"})
    assert r.status_code == 200, r.text
    assert c.get(f"/api/parts/{part}").json()["data"]["name"] == "Renamed part"


# ---------------------------------------------------------------------------
# Workspace isolation — the unit-match trigger reads `parts`.
# ---------------------------------------------------------------------------


def test_unit_trigger_is_not_a_cross_workspace_existence_oracle() -> None:
    """A foreign `part_id` must produce the workspace error, never a unit
    error.

    BEFORE ROW triggers fire in alphabetical name order, so
    `stock_entries_unit_match_check` runs *before*
    `stock_entries_workspace_fk_check` and gets to speak first. If its
    lookup were not scoped by `workspace_id`, workspace B's part being
    measured in metres would leak straight out in the error text — telling
    A that the id exists, belongs to someone else, and what it measures.

    The foreign part is deliberately given a non-default unit so an
    unscoped lookup would fail this test loudly rather than coincidentally
    agreeing on `'pcs'`.
    """
    a = TestClient(app)
    b = TestClient(app)
    ws_a = _signup(a, f"a-{uuid.uuid4().hex[:6]}@x.com")
    _signup(b, f"b-{uuid.uuid4().hex[:6]}@x.com")
    part_b = _create_part(b, "B's wire")
    _force_part_unit(part_b, "m")

    with pytest.raises(_TRIGGER_ERRORS) as excinfo:
        _raw_insert(workspace_id=ws_a, part_id=part_b, unit=DEFAULT_UNIT)

    message = str(excinfo.value)
    assert "not in workspace" in message
    assert "unit_of_measure" not in message
    assert "'m'" not in message and " m)" not in message


def test_part_unit_change_ignores_another_workspaces_ledger() -> None:
    """The freeze probe is scoped by `workspace_id` as well as `part_id`.

    Workspace B having a busy ledger must not freeze workspace A's fresh
    part, and — the direction that actually matters — the scoping must not
    let a row hide: parts and their ledger rows always share a workspace
    (0050 enforces it), so there is no row the scope can miss.
    """
    a = TestClient(app)
    b = TestClient(app)
    _signup(a, f"a-{uuid.uuid4().hex[:6]}@x.com")
    _signup(b, f"b-{uuid.uuid4().hex[:6]}@x.com")

    part_b = _create_part(b, "B's stocked part")
    _add_stock(b, part_b, 50)
    part_a = _create_part(a, "A's fresh part")

    _force_part_unit(part_a, "m")
    assert _part_unit(part_a) == "m"

    # ...and B's own part is still frozen, from its own rows.
    with pytest.raises(_TRIGGER_ERRORS):
        _force_part_unit(part_b, "m")


def test_workspace_a_cannot_read_or_write_workspace_bs_part_unit() -> None:
    """No route exposes `unit_of_measure` yet; this pins that it stays that
    way until the step that opens it does so deliberately. A part payload
    that leaked another workspace's unit would be an isolation break in the
    ordinary sense, not just a trigger question."""
    a = TestClient(app)
    b = TestClient(app)
    _signup(a, f"a-{uuid.uuid4().hex[:6]}@x.com")
    _signup(b, f"b-{uuid.uuid4().hex[:6]}@x.com")
    part_b = _create_part(b, "B's part")
    _force_part_unit(part_b, "m")

    assert a.get(f"/api/parts/{part_b}").status_code == 404
    assert a.patch(
        f"/api/parts/{part_b}", json={"unit_of_measure": "kg"}
    ).status_code in (404, 422)
    assert _part_unit(part_b) == "m"


def _pgcode(exc: BaseException) -> str | None:
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None)
    if sqlstate:
        return sqlstate
    diag = getattr(orig, "diag", None)
    return getattr(diag, "sqlstate", None)
