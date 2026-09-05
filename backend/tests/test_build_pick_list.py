"""Printable pick lists (Track B4).

Covers the properties the sheet has to hold:

* per-location breakdown — the operator is told *which shelf*, with a
  per-location quantity, not just a total;
* walk order — stops sorted by storage-location name, unassigned stock
  last, so the shelves are walked once;
* quantities come from `_required`, so both attrition sources reach the
  paper exactly as they reach reservations and consumption;
* per-stage filtering — a staged build's sheet covers this stage's lines
  at this stage's slice, and nothing else;
* shortfalls are flagged explicitly rather than quietly under-picked;
* quantities stay `Decimal` through the stock-service roll-up — no
  truncating `int()` on the `Numeric(18, 6)` columns alembic 0074
  introduced.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.domain.stock.models import StockEntry
from app.domain.stock.service import bulk_stock_by_location
from app.infra.db import SessionLocal
from app.main import app
from tests._factories import (
    add_stock as _add_stock,
    create_part as _create_part,
    create_storage as _create_storage,
    signup_user,
)


@pytest.fixture
def authed():
    c = TestClient(app)
    signup_user(c)
    return c


def _project_with_entries(c: TestClient, name: str, rows: list[dict]) -> tuple[str, list[dict]]:
    """Create a project + BOM entries forwarding every field (notably
    `attrition_pct` and `designators`), returning (project_id, entries)."""
    r = c.post("/api/projects", json={"name": name})
    assert r.status_code in (200, 201), r.text
    pid = r.json()["data"]["id"]
    for row in rows:
        r = c.post(f"/api/projects/{pid}/entries", json=row)
        assert r.status_code in (200, 201), r.text
    return pid, c.get(f"/api/projects/{pid}/entries").json()["data"]


def _build(c: TestClient, project_id: str, quantity: int = 1, name: str = "B") -> str:
    r = c.post(
        "/api/builds", json={"name": name, "project_id": project_id, "quantity": quantity}
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _sheet(c: TestClient, build_id: str, stage_id: str | None = None) -> dict:
    url = (
        f"/api/builds/{build_id}/pick-list"
        if stage_id is None
        else f"/api/builds/{build_id}/stages/{stage_id}/pick-list"
    )
    r = c.get(url)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _line(sheet: dict, part_id: str) -> dict:
    return next(line for line in sheet["lines"] if line["part_id"] == part_id)


# --- Per-location breakdown --------------------------------------------------


def test_pick_list_breaks_a_line_down_per_storage_location(authed):
    """The point of the sheet: 150 needed and split across two bins comes
    back as two picks with per-location quantities, not one total."""
    c = authed
    part = _create_part(c, "R10k")
    shelf_b = _create_storage(c, "B2 shelf")
    shelf_a = _create_storage(c, "A1 shelf")
    _add_stock(c, part, 100, shelf_a)
    _add_stock(c, part, 80, shelf_b)
    project, _ = _project_with_entries(c, "P", [{"part_id": part, "quantity": 150}])
    sheet = _sheet(c, _build(c, project))

    line = _line(sheet, part)
    assert line["required"] == 150
    assert line["on_hand"] == 180
    assert line["planned"] == 150
    assert line["short_by"] == 0
    assert line["is_short"] is False
    assert line["location_count"] == 2

    # Largest bucket first: 100 from A1, the 50 balance from B2.
    picks = {
        stop["storage_location_name"]: stop["picks"][0]["quantity"]
        for stop in sheet["stops"]
    }
    assert picks == {"A1 shelf": 100, "B2 shelf": 50}
    assert sheet["totals"] == {"lines": 1, "short_lines": 0, "stops": 2}


def test_stops_are_sorted_by_location_with_unassigned_last(authed):
    """Walk order. Named shelves alphabetically; stock in no location is a
    real (last) stop rather than being silently dropped."""
    c = authed
    part = _create_part(c, "C100n")
    zulu = _create_storage(c, "Z9 shelf")
    alpha = _create_storage(c, "A1 shelf")
    _add_stock(c, part, 10, zulu)
    _add_stock(c, part, 10, alpha)
    _add_stock(c, part, 10)  # no storage location at all
    project, _ = _project_with_entries(c, "P", [{"part_id": part, "quantity": 30}])
    sheet = _sheet(c, _build(c, project))

    assert [s["storage_location_name"] for s in sheet["stops"]] == [
        "A1 shelf",
        "Z9 shelf",
        "Unassigned",
    ]
    assert sheet["stops"][-1]["storage_location_id"] is None
    assert _line(sheet, part)["planned"] == 30


def test_one_stop_lists_every_part_it_serves(authed):
    """A bin holding three BOM lines is visited once, and the stop names
    all three parts — that is what "walk the shelves once" means."""
    c = authed
    bin_a = _create_storage(c, "A1 shelf")
    parts = [_create_part(c, name) for name in ("R1k", "C100n", "D1N4148")]
    for part in parts:
        _add_stock(c, part, 50, bin_a)
    project, _ = _project_with_entries(
        c, "P", [{"part_id": p, "quantity": 5} for p in parts]
    )
    sheet = _sheet(c, _build(c, project))

    assert len(sheet["stops"]) == 1
    stop = sheet["stops"][0]
    assert stop["storage_location_name"] == "A1 shelf"
    assert {p["part_name"] for p in stop["picks"]} == {"R1k", "C100n", "D1N4148"}
    assert all(p["quantity"] == 5 for p in stop["picks"])


def test_lot_identity_rides_along_on_each_pick(authed):
    """Stock is bucketed per (storage, lot), so a shelf holding two lots of
    the same part becomes two picks the operator can tell apart."""
    c = authed
    part = _create_part(c, "R10k")
    shelf = _create_storage(c, "A1 shelf")
    _add_stock(c, part, 40, shelf, lot_name="LOT-OLD")
    _add_stock(c, part, 10, shelf, lot_name="LOT-NEW")
    project, _ = _project_with_entries(c, "P", [{"part_id": part, "quantity": 45}])
    sheet = _sheet(c, _build(c, project))

    picks = sheet["stops"][0]["picks"]
    assert {(p["lot_name"], p["quantity"]) for p in picks} == {
        ("LOT-OLD", 40),
        ("LOT-NEW", 5),
    }

    # Two lots on one shelf are two picks but ONE stop on the walk.
    # `location_count` heads a column labelled "Locations", so it counts
    # distinct locations; counting picks would print "2 locations" above a
    # route with a single stop.
    assert len(picks) == 2
    assert sheet["totals"]["stops"] == 1
    assert _line(sheet, part)["location_count"] == 1


# --- Quantities come from `_required` ---------------------------------------


def test_required_is_attrition_adjusted_via_required(authed):
    """Both attrition sources compound into the picked quantity, exactly as
    `_required` computes them: ceil(100 x 1.10 x 1.25) = 138 — not 100, and
    not the 137.5 an un-ceiled multiplication would give."""
    c = authed
    part = _create_part(c, "R10k", attrition_percentage=10)
    shelf = _create_storage(c, "A1 shelf")
    _add_stock(c, part, 500, shelf)
    project, _ = _project_with_entries(
        c, "P", [{"part_id": part, "quantity": 100, "attrition_pct": 25}]
    )
    sheet = _sheet(c, _build(c, project, quantity=1))

    line = _line(sheet, part)
    assert line["required"] == 138
    assert line["attrition_pct"] == 25.0
    assert sheet["stops"][0]["picks"][0]["quantity"] == 138


def test_required_scales_with_build_quantity(authed):
    c = authed
    part = _create_part(c, "R10k")
    shelf = _create_storage(c, "A1 shelf")
    _add_stock(c, part, 500, shelf)
    project, _ = _project_with_entries(c, "P", [{"part_id": part, "quantity": 7}])
    sheet = _sheet(c, _build(c, project, quantity=6))
    assert _line(sheet, part)["required"] == 42


def test_dnp_and_non_part_lines_are_not_picked(authed):
    """The sheet uses the same consumable filter as reservations: DNP and
    documentation-only rows are not things anyone fetches."""
    c = authed
    real = _create_part(c, "R10k")
    skipped = _create_part(c, "TP1")
    shelf = _create_storage(c, "A1 shelf")
    _add_stock(c, real, 50, shelf)
    _add_stock(c, skipped, 50, shelf)
    project, _ = _project_with_entries(
        c,
        "P",
        [
            {"part_id": real, "quantity": 5},
            {"part_id": skipped, "quantity": 5, "dnp": True},
            {"entry_type": "non_part", "name": "PCB fab", "quantity": 1},
        ],
    )
    sheet = _sheet(c, _build(c, project))
    assert [line["part_id"] for line in sheet["lines"]] == [real]


def test_unit_is_surfaced_next_to_every_quantity(authed):
    """`parts.unit_of_measure` (alembic 0074) rides along so "138" on paper
    is never ambiguous between pieces and metres."""
    c = authed
    part = _create_part(c, "Wire")
    shelf = _create_storage(c, "A1 shelf")
    _add_stock(c, part, 10, shelf)
    project, _ = _project_with_entries(c, "P", [{"part_id": part, "quantity": 4}])
    sheet = _sheet(c, _build(c, project))

    assert _line(sheet, part)["unit"] == "pcs"
    assert sheet["stops"][0]["picks"][0]["unit"] == "pcs"


# --- Shortfalls --------------------------------------------------------------


def test_shortfall_is_flagged_explicitly(authed):
    """Under-stocked lines are picked down to what exists and flagged —
    never silently rounded down to "the plan is fine"."""
    c = authed
    part = _create_part(c, "R10k")
    shelf = _create_storage(c, "A1 shelf")
    _add_stock(c, part, 30, shelf)
    project, _ = _project_with_entries(c, "P", [{"part_id": part, "quantity": 100}])
    sheet = _sheet(c, _build(c, project))

    line = _line(sheet, part)
    assert line["required"] == 100
    assert line["on_hand"] == 30
    assert line["planned"] == 30
    assert line["short_by"] == 70
    assert line["is_short"] is True
    assert sheet["totals"]["short_lines"] == 1
    # The 30 that *do* exist are still on the sheet — a short line is a
    # partial pick, not a skipped one.
    assert sheet["stops"][0]["picks"][0]["quantity"] == 30


def test_line_with_no_stock_at_all_is_short_with_no_stops(authed):
    c = authed
    part = _create_part(c, "R10k")
    project, _ = _project_with_entries(c, "P", [{"part_id": part, "quantity": 5}])
    sheet = _sheet(c, _build(c, project))

    line = _line(sheet, part)
    assert (line["planned"], line["short_by"], line["is_short"]) == (0, 5, True)
    assert line["location_count"] == 0
    assert sheet["stops"] == []


def test_two_bom_lines_for_one_part_share_a_single_pool(authed):
    """The same part on two BOM lines draws from the same shelves.

    Allocating each line against a fresh copy of the buckets would hand
    both lines the same reel: two lines of 10 against a bin holding 12
    would each print "take 10, short 0", the operator would find 12 on the
    shelf, and the consume step would then refuse the build with
    "insufficient stock (have 12, want 20)". The second line has to be
    short by 8 on paper, before anyone walks anywhere.

    `project_entries` has no unique constraint on `(project_id, part_id)`
    and neither the create-entry route nor BOM import dedupes, so this is a
    reachable BOM, not a contrived one.
    """
    c = authed
    part = _create_part(c, "R10k")
    shelf = _create_storage(c, "A1 shelf")
    _add_stock(c, part, 12, shelf)
    project, _ = _project_with_entries(
        c, "P", [{"part_id": part, "quantity": 10}, {"part_id": part, "quantity": 10}]
    )
    sheet = _sheet(c, _build(c, project))

    first, second = sheet["lines"]
    # BOM order decides who gets the stock: the sheet reads top to bottom.
    assert (first["required"], first["planned"], first["short_by"]) == (10, 10, 0)
    assert (second["required"], second["planned"], second["short_by"]) == (10, 2, 8)
    assert second["is_short"] is True
    assert sheet["totals"]["short_lines"] == 1

    # Never more than the shelf holds.
    picks = sheet["stops"][0]["picks"]
    assert sum(p["quantity"] for p in picks) == 12
    assert all(p["available"] == 12 for p in picks)
    # `on_hand` stays the part's own total — it can exceed a line's
    # `planned` precisely because the pool is shared.
    assert first["on_hand"] == second["on_hand"] == 12


def test_substitutes_are_reported_but_not_silently_picked(authed):
    """A registered substitute does not quietly satisfy a short line: using
    one is an explicit per-line decision at consume time, and a sheet that
    sent the operator after a part the consume screen was never told about
    would be worse than one that says "short 5".

    It IS reported, though — `alternates_available` mirrors what
    `shortage_analysis` shows on the build screen, so the two never
    disagree about whether the build is covered.
    """
    c = authed
    main = _create_part(c, "R10k")
    sub = _create_part(c, "R10k alt")
    shelf = _create_storage(c, "A1 shelf")
    _add_stock(c, sub, 500, shelf)
    r = c.post(
        f"/api/parts/{main}/substitutes",
        json={"substitute_part_id": sub, "direction": "bidirectional"},
    )
    assert r.status_code in (200, 201), r.text
    project, _ = _project_with_entries(c, "P", [{"part_id": main, "quantity": 5}])
    build = _build(c, project)
    sheet = _sheet(c, build)

    line = _line(sheet, main)
    assert line["is_short"] is True and line["short_by"] == 5
    assert line["alternates_available"] == 500
    assert sheet["stops"] == []

    # Same number the build screen shows, so the two views agree.
    shortage = c.get(f"/api/builds/{build}").json()["data"]["shortage"]
    assert shortage[0]["substitute_available"] == line["alternates_available"]


# --- Per-stage ---------------------------------------------------------------


def _stage(c: TestClient, build_id: str, name: str, lines: list[dict]) -> str:
    r = c.post(f"/api/builds/{build_id}/stages", json={"name": name, "lines": lines})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def test_stage_pick_list_covers_only_that_stages_lines(authed):
    """A staged build's picker wants this stage's parts. The whole-build
    sheet would have them fetch the next stage's material too."""
    c = authed
    smt = _create_part(c, "R10k")
    tht = _create_part(c, "Header")
    shelf = _create_storage(c, "A1 shelf")
    _add_stock(c, smt, 500, shelf)
    _add_stock(c, tht, 500, shelf)
    project, entries = _project_with_entries(
        c, "P", [{"part_id": smt, "quantity": 10}, {"part_id": tht, "quantity": 4}]
    )
    build = _build(c, project)
    smt_entry = next(e for e in entries if e["part_id"] == smt)
    tht_entry = next(e for e in entries if e["part_id"] == tht)
    stage_1 = _stage(c, build, "SMT", [{"project_entry_id": smt_entry["id"]}])
    _stage(c, build, "THT", [{"project_entry_id": tht_entry["id"]}])

    whole = _sheet(c, build)
    assert {line["part_id"] for line in whole["lines"]} == {smt, tht}

    staged = _sheet(c, build, stage_id=stage_1)
    assert [line["part_id"] for line in staged["lines"]] == [smt]
    assert staged["stage"]["name"] == "SMT"
    assert staged["stage"]["sequence"] == 0
    assert staged["stops"][0]["picks"] == [
        {
            "project_entry_id": smt_entry["id"],
            "part_id": smt,
            "part_name": "R10k",
            "mpn": None,
            "designators": [],
            "lot_id": None,
            "lot_name": None,
            "quantity": 10,
            "unit": "pcs",
            "available": 500,
        }
    ]


def test_stage_quantities_are_the_stages_slice_of_required(authed):
    """Stage portions slice `_required`'s output cumulatively, so two 50%
    stages of a 138-unit requirement pick 69 + 69 — never 2 x ceil(69) = 140
    and never 2 x 100 from re-deriving `project_entries.quantity`."""
    c = authed
    part = _create_part(c, "R10k", attrition_percentage=10)
    shelf = _create_storage(c, "A1 shelf")
    _add_stock(c, part, 500, shelf)
    project, entries = _project_with_entries(
        c, "P", [{"part_id": part, "quantity": 100, "attrition_pct": 25}]
    )
    build = _build(c, project)
    entry_id = entries[0]["id"]
    first = _stage(c, build, "half 1", [{"project_entry_id": entry_id, "portion_pct": 50}])
    second = _stage(c, build, "half 2", [{"project_entry_id": entry_id, "portion_pct": 50}])

    whole = _line(_sheet(c, build), part)
    sheet_1 = _sheet(c, build, stage_id=first)
    sheet_2 = _sheet(c, build, stage_id=second)

    assert whole["required"] == 138
    assert _line(sheet_1, part)["required"] == 69
    assert _line(sheet_2, part)["required"] == 69
    assert _line(sheet_1, part)["required"] + _line(sheet_2, part)["required"] == 138
    assert _line(sheet_1, part)["portion_pct"] == 50.0


def test_whole_build_sheet_reports_no_stage(authed):
    c = authed
    part = _create_part(c, "R10k")
    shelf = _create_storage(c, "A1 shelf")
    _add_stock(c, part, 10, shelf)
    project, _ = _project_with_entries(c, "P", [{"part_id": part, "quantity": 2}])
    sheet = _sheet(c, _build(c, project))
    assert sheet["stage"] is None
    assert sheet["lines"][0]["portion_pct"] is None


def test_stage_pick_list_404s_for_a_stage_of_another_build(authed):
    """Same gate `POST /stages/{id}/consume` uses: a stage of build A can't
    be printed through build B's URL."""
    c = authed
    part = _create_part(c, "R10k")
    project, entries = _project_with_entries(c, "P", [{"part_id": part, "quantity": 2}])
    build_a = _build(c, project, name="A")
    build_b = _build(c, project, name="B")
    stage_a = _stage(c, build_a, "S", [{"project_entry_id": entries[0]["id"]}])

    r = c.get(f"/api/builds/{build_b}/stages/{stage_a}/pick-list")
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "build_stage.not_found"


def test_unknown_build_404s(authed):
    r = authed.get(f"/api/builds/{uuid.uuid4()}/pick-list")
    assert r.status_code == 404
    assert r.json()["code"] == "build.not_found"


# --- Ledger roll-up ----------------------------------------------------------


def test_bulk_stock_by_location_preserves_fractional_quantities(authed):
    """`stock_entries.quantity_delta` is `Numeric(18, 6)` since alembic
    0074. The roll-up the pick list reads must hand back a `Decimal` — an
    `int()` here would turn 2.5 m of wire into 2 m, which is exactly the
    truncation 0074 spent a migration making impossible."""
    c = authed
    part = _create_part(c, "Wire")
    storage = _create_storage(c, "A1 shelf")
    _add_stock(c, part, 2, storage)

    with SessionLocal() as s:
        row = s.execute(
            StockEntry.__table__.select().where(
                StockEntry.part_id == uuid.UUID(part)
            )
        ).first()
        # Append a fractional delta straight onto the ledger — no API
        # schema accepts one yet, but the column holds it and the roll-up
        # must not lose it.
        s.add(
            StockEntry(
                workspace_id=row.workspace_id,
                part_id=uuid.UUID(part),
                storage_location_id=uuid.UUID(storage),
                quantity_delta=Decimal("0.5"),
                status="on_hand",
                operation_type="adjust",
                occurred_at=row.occurred_at,
            )
        )
        s.commit()
        buckets = bulk_stock_by_location(
            s, workspace_id=row.workspace_id, part_ids=[uuid.UUID(part)]
        )

    quantity = buckets[uuid.UUID(part)][0]["quantity"]
    assert isinstance(quantity, Decimal)
    assert quantity == Decimal("2.5")
    # The ledger's own unit stamp is part of the bucket key, so a later
    # edit to `parts.unit_of_measure` can never relabel written history.
    assert buckets[uuid.UUID(part)][0]["unit"] == "pcs"


def test_bulk_stock_by_location_is_workspace_scoped():
    """Direct cover for the roll-up's own `workspace_id` filter.

    The route-level isolation test in `test_workspace_isolation.py` cannot
    reach this: the part ids it feeds in are already workspace-scoped, and
    part UUIDs are globally unique, so dropping the filter here would leave
    every HTTP-level assertion green. Asking as the wrong workspace has to
    be tested against the function.
    """
    a = TestClient(app)
    b = TestClient(app)
    signup_user(a)
    ws_b = uuid.UUID(signup_user(b).json()["data"]["workspace_id"])

    part_a = _create_part(a, "A part")
    _add_stock(a, part_a, 40, _create_storage(a, "A shelf"))

    with SessionLocal() as s:
        assert bulk_stock_by_location(
            s, workspace_id=ws_b, part_ids=[uuid.UUID(part_a)]
        ) == {}


def test_pick_list_writes_no_ledger_or_audit_rows(authed):
    """Read-only: printing a sheet must not reserve, consume, or otherwise
    move stock, which is why the routes emit no `audit_log` row either."""
    c = authed
    part = _create_part(c, "R10k")
    shelf = _create_storage(c, "A1 shelf")
    _add_stock(c, part, 50, shelf)
    project, _ = _project_with_entries(c, "P", [{"part_id": part, "quantity": 5}])
    build = _build(c, project)

    def _counts() -> tuple[int, int]:
        return (
            len(c.get("/api/stock/history").json()["data"]),
            len(c.get("/api/audit").json()["data"]),
        )

    before = _counts()
    _sheet(c, build)
    _sheet(c, build)
    assert _counts() == before
