"""Kitting (Track B3).

Covers the properties the feature has to hold:

* a kit is a **move** — total on-hand per part is invariant, only the
  distribution across locations changes, and every relocated unit is a
  matched `move_out` / `move_in` pair in the ledger;
* kit quantities come from `_required` (whole build) or its per-stage
  slice, so both attrition sources compound into them exactly once;
* the kit **tops up** the staging location, so re-running it is a no-op;
* partial availability moves what exists and reports the shortfall;
* reservations are untouched — `reserved_quantity` is invariant;
* the whole-build endpoint is refused for a staged build, mirroring
  consume;
* the preview route writes nothing.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.domain.audit.models import AuditLog
from app.domain.stock.models import StockEntry
from app.infra.db import SessionLocal
from app.main import app
from tests._factories import add_stock as _add_stock
from tests._factories import create_part as _create_part
from tests._factories import create_storage as _create_storage
from tests._factories import signup_user


@pytest.fixture
def authed():
    c = TestClient(app)
    signup_user(c)
    return c


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
        "/api/builds", json={"name": name, "project_id": project_id, "quantity": quantity}
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _kit(c: TestClient, build_id: str, storage_id: str):
    return c.post(f"/api/builds/{build_id}/kit", json={"storage_location_id": storage_id})


def _at(c: TestClient, storage_id: str, part_id: str) -> int:
    """On-hand of one part at one storage location, via the ledger roll-up."""
    rows = c.get(f"/api/parts/{part_id}/stock").json()["data"]["rows"]
    return sum(
        row["quantity"] for row in rows if row.get("storage_location_id") == storage_id
    )


def _total(c: TestClient, part_id: str) -> int:
    return c.get(f"/api/parts/{part_id}/stock").json()["data"]["total_on_hand"]


def _reserved_total(part_id: str) -> Decimal:
    with SessionLocal() as s:
        return Decimal(
            s.execute(
                select(func.coalesce(func.sum(StockEntry.quantity_delta), 0))
                .where(StockEntry.part_id == uuid.UUID(part_id))
                .where(StockEntry.status == "reserved")
            ).scalar_one()
        )


def _move_rows(build_id: str) -> list[StockEntry]:
    with SessionLocal() as s:
        return list(
            s.execute(
                select(StockEntry)
                .where(StockEntry.build_id == uuid.UUID(build_id))
                .where(StockEntry.operation_type.in_(("move_out", "move_in")))
                .order_by(StockEntry.occurred_at, StockEntry.id)
            ).scalars()
        )


def _line(body: dict, part_id: str) -> dict:
    return next(row for row in body["lines"] if row["part_id"] == part_id)


# --- The core operation ------------------------------------------------------


def test_kit_consolidates_from_every_bin_into_the_staging_location(authed):
    """The whole point: one call, stock from several shelves lands on one tray.

    Largest bucket first, so the operator visits as few bins as possible;
    the tail bucket is taken partially.
    """
    c = authed
    part = _create_part(c, "R1k 0402")
    shelf_a = _create_storage(c, "Shelf A")
    shelf_b = _create_storage(c, "Shelf B")
    tray = _create_storage(c, "Kitting tray")
    _add_stock(c, part, 30, shelf_a)
    _add_stock(c, part, 80, shelf_b)

    project_id = _project(c, "PCB-KIT")
    _add_entry(c, project_id, part_id=part, quantity=10)
    bid = _build(c, project_id, quantity=10)  # required = 100

    r = _kit(c, bid, tray)
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["executed"] is True
    assert body["storage_location_name"] == "Kitting tray"

    row = _line(body, part)
    assert row["required"] == 100
    assert row["at_staging"] == 0
    assert row["to_move"] == 100
    assert row["moving"] == 100
    assert row["short_by"] == 0
    # Largest bucket first: 80 from shelf B, then the 20 balance from shelf A.
    assert [(s["storage_location_id"], s["quantity"]) for s in row["sources"]] == [
        (shelf_b, 80),
        (shelf_a, 20),
    ]
    assert body["totals"] == {"lines": 1, "moving": 100, "short_by": 0, "short_lines": 0}

    # The physical result.
    assert _at(c, tray, part) == 100
    assert _at(c, shelf_a, part) == 10
    assert _at(c, shelf_b, part) == 0
    # A kit is a MOVE: nothing was created or destroyed.
    assert _total(c, part) == 110


def test_kit_writes_matched_move_pairs_tagged_with_the_build(authed):
    """Every relocated unit is a `move_out`/`move_in` pair through the stock
    service — not a bespoke ledger row — and both sides carry `build_id` so
    the kit shows up on the build's activity timeline."""
    c = authed
    part = _create_part(c, "C100n")
    shelf = _create_storage(c, "Shelf")
    tray = _create_storage(c, "Tray")
    _add_stock(c, part, 50, shelf)

    project_id = _project(c, "PCB-LEDGER")
    _add_entry(c, project_id, part_id=part, quantity=5)
    bid = _build(c, project_id, quantity=4)  # required = 20

    assert _kit(c, bid, tray).status_code == 200

    rows = _move_rows(bid)
    assert len(rows) == 2
    out_row = next(r for r in rows if r.operation_type == "move_out")
    in_row = next(r for r in rows if r.operation_type == "move_in")
    assert out_row.quantity_delta == Decimal(-20)
    assert in_row.quantity_delta == Decimal(20)
    assert out_row.storage_location_id == uuid.UUID(shelf)
    assert in_row.storage_location_id == uuid.UUID(tray)
    # Matched pair, both tagged with the build, neither with a stage.
    assert out_row.related_entry_id == in_row.id
    assert in_row.related_entry_id == out_row.id
    assert out_row.build_id == in_row.build_id == uuid.UUID(bid)
    assert out_row.build_stage_id is None and in_row.build_stage_id is None
    assert out_row.status == in_row.status == "on_hand"

    # …and therefore on the build activity feed.
    events = c.get(f"/api/builds/{bid}/activity").json()["data"]["events"]
    assert {"move_out", "move_in"} <= {e["operation_type"] for e in events}


def test_kit_is_idempotent_it_tops_the_tray_up_rather_than_adding_to_it(authed):
    """A retried request / double-clicked button must not build a second tray."""
    c = authed
    part = _create_part(c, "L10u")
    shelf = _create_storage(c, "Shelf")
    tray = _create_storage(c, "Tray")
    _add_stock(c, part, 500, shelf)

    project_id = _project(c, "PCB-IDEMPOTENT")
    _add_entry(c, project_id, part_id=part, quantity=25)
    bid = _build(c, project_id, quantity=4)  # required = 100

    first = _kit(c, bid, tray).json()["data"]
    assert first["totals"]["moving"] == 100

    second = _kit(c, bid, tray)
    assert second.status_code == 200, second.text
    body = second.json()["data"]
    row = _line(body, part)
    assert row["at_staging"] == 100
    assert row["to_move"] == 0
    assert row["moving"] == 0
    assert row["sources"] == []
    assert body["totals"]["moving"] == 0

    assert _at(c, tray, part) == 100
    assert _at(c, shelf, part) == 400
    # The second call wrote no ledger rows at all.
    assert len(_move_rows(bid)) == 2


def test_kit_tops_up_a_partially_stocked_tray(authed):
    """Half the tray already there → only the balance moves."""
    c = authed
    part = _create_part(c, "D1N4148")
    shelf = _create_storage(c, "Shelf")
    tray = _create_storage(c, "Tray")
    _add_stock(c, part, 40, tray)
    _add_stock(c, part, 200, shelf)

    project_id = _project(c, "PCB-TOPUP")
    _add_entry(c, project_id, part_id=part, quantity=100)
    bid = _build(c, project_id, quantity=1)  # required = 100

    row = _line(_kit(c, bid, tray).json()["data"], part)
    assert row["required"] == 100
    assert row["at_staging"] == 40
    assert row["to_move"] == 60
    assert row["moving"] == 60
    assert _at(c, tray, part) == 100
    assert _at(c, shelf, part) == 140


# --- Partial availability ----------------------------------------------------


def test_kit_moves_what_exists_and_reports_the_shortfall(authed):
    """Documented policy: move what exists, report the rest. Refusing would
    hand the operator nothing and force the shelf-walk kitting exists to
    remove."""
    c = authed
    short_part = _create_part(c, "U1 MCU")
    ok_part = _create_part(c, "R10k")
    shelf = _create_storage(c, "Shelf")
    tray = _create_storage(c, "Tray")
    _add_stock(c, short_part, 30, shelf)
    _add_stock(c, ok_part, 500, shelf)

    project_id = _project(c, "PCB-SHORT")
    _add_entry(c, project_id, part_id=short_part, quantity=1)
    _add_entry(c, project_id, part_id=ok_part, quantity=4)
    bid = _build(c, project_id, quantity=100)  # 100 and 400 required

    r = _kit(c, bid, tray)
    assert r.status_code == 200, r.text
    body = r.json()["data"]

    short_row = _line(body, short_part)
    assert short_row["required"] == 100
    assert short_row["moving"] == 30
    assert short_row["short_by"] == 70

    ok_row = _line(body, ok_part)
    assert ok_row["moving"] == 400
    assert ok_row["short_by"] == 0

    assert body["totals"]["short_lines"] == 1
    assert body["totals"]["short_by"] == 70

    # The available material still moved — the kit is not all-or-nothing on
    # availability (it IS all-or-nothing on failure).
    assert _at(c, tray, short_part) == 30
    assert _at(c, tray, ok_part) == 400


def test_kit_with_nothing_available_moves_nothing_and_still_succeeds(authed):
    c = authed
    part = _create_part(c, "X1 crystal")
    tray = _create_storage(c, "Tray")

    project_id = _project(c, "PCB-EMPTY")
    _add_entry(c, project_id, part_id=part, quantity=2)
    bid = _build(c, project_id, quantity=1)

    body = _kit(c, bid, tray).json()["data"]
    assert _line(body, part) == {
        **_line(body, part),
        "required": 2,
        "moving": 0,
        "short_by": 2,
        "sources": [],
    }
    assert _move_rows(bid) == []


# --- `_required` is the only quantity authority ------------------------------


def test_kit_quantities_carry_both_attrition_sources(authed):
    """`100 × 1.10 × 1.25 = 137.5 → 138` — the same number the shortage table
    shows, because both read `_required`. A kit that re-derived demand from
    `project_entries.quantity` would put 100 on the tray."""
    c = authed
    part = _create_part(c, "R1k", attrition_percentage=10)
    shelf = _create_storage(c, "Shelf")
    tray = _create_storage(c, "Tray")
    _add_stock(c, part, 500, shelf)

    project_id = _project(c, "PCB-ATTR-KIT")
    _add_entry(c, project_id, part_id=part, quantity=100, attrition_pct=25)
    bid = _build(c, project_id, quantity=1)

    shortage = c.get(f"/api/builds/{bid}").json()["data"]["shortage"]
    assert shortage[0]["required"] == 138

    row = _line(_kit(c, bid, tray).json()["data"], part)
    assert row["required"] == 138
    assert row["moving"] == 138
    assert _at(c, tray, part) == 138


def test_two_bom_lines_of_the_same_part_are_one_pile_on_the_tray(authed):
    """Duplicate BOM lines aggregate per part before any bucket is picked —
    planning them separately would let each line claim the same reel."""
    c = authed
    part = _create_part(c, "R100")
    shelf = _create_storage(c, "Shelf")
    tray = _create_storage(c, "Tray")
    _add_stock(c, part, 100, shelf)

    project_id = _project(c, "PCB-DUP")
    _add_entry(c, project_id, part_id=part, quantity=3)
    _add_entry(c, project_id, part_id=part, quantity=4)
    bid = _build(c, project_id, quantity=10)  # 30 + 40 = 70

    body = _kit(c, bid, tray).json()["data"]
    assert len(body["lines"]) == 1
    row = body["lines"][0]
    assert len(row["project_entry_ids"]) == 2
    assert row["required"] == 70
    assert row["moving"] == 70
    assert _at(c, tray, part) == 70
    assert _at(c, shelf, part) == 30


# --- Reservations ------------------------------------------------------------


def test_kit_leaves_reservations_untouched(authed):
    """Reserve rows carry no storage location and a kit writes only `on_hand`
    rows, so `reserved_quantity` cannot be double-counted or stranded by
    moving the material it covers."""
    c = authed
    part = _create_part(c, "Q1")
    shelf = _create_storage(c, "Shelf")
    tray = _create_storage(c, "Tray")
    _add_stock(c, part, 200, shelf)

    project_id = _project(c, "PCB-RESERVE")
    _add_entry(c, project_id, part_id=part, quantity=10)
    bid = _build(c, project_id, quantity=5)  # reserves 50

    before = _reserved_total(part)
    assert before == Decimal(50)

    assert _kit(c, bid, tray).status_code == 200
    assert _reserved_total(part) == before

    with SessionLocal() as s:
        kit_reserved_rows = s.execute(
            select(func.count())
            .select_from(StockEntry)
            .where(StockEntry.build_id == uuid.UUID(bid))
            .where(StockEntry.status == "reserved")
            .where(StockEntry.operation_type.in_(("move_out", "move_in")))
        ).scalar_one()
    assert kit_reserved_rows == 0

    # And the build still consumes normally afterwards, from the tray.
    entry_id = c.get(f"/api/projects/{project_id}/entries").json()["data"][0]["id"]
    r = c.post(
        f"/api/builds/{bid}/consume",
        json={
            "lines": [
                {
                    "project_entry_id": entry_id,
                    "part_id": part,
                    "quantity": 50,
                    "storage_location_id": tray,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert _reserved_total(part) == Decimal(0)
    assert _at(c, tray, part) == 0
    assert _total(c, part) == 150


# --- Preview -----------------------------------------------------------------


def test_kit_plan_writes_nothing(authed):
    c = authed
    part = _create_part(c, "R220")
    shelf = _create_storage(c, "Shelf")
    tray = _create_storage(c, "Tray")
    _add_stock(c, part, 100, shelf)

    project_id = _project(c, "PCB-PLAN")
    _add_entry(c, project_id, part_id=part, quantity=6)
    bid = _build(c, project_id, quantity=10)  # 60

    r = c.get(f"/api/builds/{bid}/kit-plan", params={"storage_location_id": tray})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["executed"] is False
    assert _line(body, part)["moving"] == 60
    assert _line(body, part)["sources"][0]["storage_location_name"] == "Shelf"

    assert _move_rows(bid) == []
    assert _at(c, tray, part) == 0
    assert _at(c, shelf, part) == 100


# --- Multi-stage builds ------------------------------------------------------


def test_whole_build_kit_is_refused_once_the_build_has_stages(authed):
    """Same guard as `POST /consume`: the whole-BOM quantity is the sum of the
    stages, so a whole-build kit of a partly-consumed staged build would haul
    material stages already drew."""
    c = authed
    part = _create_part(c, "R47")
    shelf = _create_storage(c, "Shelf")
    tray = _create_storage(c, "Tray")
    _add_stock(c, part, 500, shelf)

    project_id = _project(c, "PCB-STAGED")
    entry = _add_entry(c, project_id, part_id=part, quantity=10)
    bid = _build(c, project_id, quantity=10)
    r = c.post(
        f"/api/builds/{bid}/stages",
        json={"name": "SMT", "lines": [{"project_entry_id": entry["id"], "portion_pct": 60}]},
    )
    assert r.status_code == 201, r.text

    r = _kit(c, bid, tray)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "build.has_stages"
    assert (
        c.get(f"/api/builds/{bid}/kit-plan", params={"storage_location_id": tray}).status_code
        == 400
    )
    assert _at(c, tray, part) == 0


def test_stage_kit_moves_only_that_stages_slice(authed):
    """A stage kit is the stage's allocation — a cumulative slice of
    `_required` — not the whole build's."""
    c = authed
    part = _create_part(c, "R47")
    shelf = _create_storage(c, "Shelf")
    tray = _create_storage(c, "Tray")
    _add_stock(c, part, 500, shelf)

    project_id = _project(c, "PCB-STAGE-KIT")
    entry = _add_entry(c, project_id, part_id=part, quantity=10)
    bid = _build(c, project_id, quantity=10)  # whole build needs 100
    stage_one = c.post(
        f"/api/builds/{bid}/stages",
        json={"name": "SMT", "lines": [{"project_entry_id": entry["id"], "portion_pct": 60}]},
    ).json()["data"]["id"]
    stage_two = c.post(
        f"/api/builds/{bid}/stages",
        json={"name": "THT", "lines": [{"project_entry_id": entry["id"], "portion_pct": 40}]},
    ).json()["data"]["id"]

    r = c.post(
        f"/api/builds/{bid}/stages/{stage_one}/kit",
        json={"storage_location_id": tray},
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["build_stage_id"] == stage_one
    row = _line(body, part)
    assert row["required"] == 60
    assert row["moving"] == 60
    assert _at(c, tray, part) == 60

    # The kit's ledger rows are tagged with the stage.
    rows = _move_rows(bid)
    assert {r_.build_stage_id for r_ in rows} == {uuid.UUID(stage_one)}

    # Consume stage 1 off the tray, then kit stage 2 — the tray is empty
    # again so the full 40 moves.
    r = c.post(
        f"/api/builds/{bid}/stages/{stage_one}/consume",
        json={
            "lines": [
                {
                    "project_entry_id": entry["id"],
                    "part_id": part,
                    "quantity": 60,
                    "storage_location_id": tray,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert _at(c, tray, part) == 0

    r = c.post(
        f"/api/builds/{bid}/stages/{stage_two}/kit",
        json={"storage_location_id": tray},
    )
    assert r.status_code == 200, r.text
    assert _line(r.json()["data"], part)["moving"] == 40
    assert _at(c, tray, part) == 40


def test_stage_kit_refuses_a_completed_stage(authed):
    c = authed
    part = _create_part(c, "R47")
    shelf = _create_storage(c, "Shelf")
    tray = _create_storage(c, "Tray")
    _add_stock(c, part, 500, shelf)

    project_id = _project(c, "PCB-STAGE-DONE")
    entry = _add_entry(c, project_id, part_id=part, quantity=1)
    bid = _build(c, project_id, quantity=10)
    # Two stages, so completing the first leaves the build in_progress and
    # the refusal comes from the stage guard rather than the build guard.
    stage = c.post(
        f"/api/builds/{bid}/stages",
        json={"name": "first", "lines": [{"project_entry_id": entry["id"], "portion_pct": 50}]},
    ).json()["data"]["id"]
    c.post(
        f"/api/builds/{bid}/stages",
        json={"name": "second", "lines": [{"project_entry_id": entry["id"], "portion_pct": 50}]},
    )
    r = c.post(
        f"/api/builds/{bid}/stages/{stage}/consume",
        json={
            "lines": [
                {
                    "project_entry_id": entry["id"],
                    "part_id": part,
                    "quantity": 5,
                    "storage_location_id": shelf,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text

    r = c.post(f"/api/builds/{bid}/stages/{stage}/kit", json={"storage_location_id": tray})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "build.kit_error"
    assert "already complete" in r.json()["status"]["message"]


# --- Guards ------------------------------------------------------------------


@pytest.mark.parametrize(
    "flag,message",
    [("archived", "archived"), ("full", "full")],
)
def test_kit_refuses_an_unusable_staging_location(authed, flag, message):
    c = authed
    part = _create_part(c, "R1")
    shelf = _create_storage(c, "Shelf")
    tray = _create_storage(c, "Tray")
    _add_stock(c, part, 100, shelf)

    project_id = _project(c, f"PCB-{flag}")
    _add_entry(c, project_id, part_id=part, quantity=1)
    bid = _build(c, project_id, quantity=1)

    if flag == "archived":
        assert c.post(f"/api/storage/{tray}/archive").status_code == 200
    else:
        assert c.patch(f"/api/storage/{tray}", json={"is_full": True}).status_code == 200

    r = _kit(c, bid, tray)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "build.kit_error"
    assert message in r.json()["status"]["message"]
    assert _move_rows(bid) == []


def test_kit_refuses_an_unknown_staging_location(authed):
    c = authed
    part = _create_part(c, "R2")
    project_id = _project(c, "PCB-UNKNOWN")
    _add_entry(c, project_id, part_id=part, quantity=1)
    bid = _build(c, project_id, quantity=1)

    r = _kit(c, bid, str(uuid.uuid4()))
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "build.kit_error"
    assert "staging location not found" in r.json()["status"]["message"]


def test_kit_refuses_a_completed_build(authed):
    c = authed
    part = _create_part(c, "R3")
    shelf = _create_storage(c, "Shelf")
    tray = _create_storage(c, "Tray")
    _add_stock(c, part, 100, shelf)

    project_id = _project(c, "PCB-DONE")
    entry = _add_entry(c, project_id, part_id=part, quantity=2)
    bid = _build(c, project_id, quantity=5)
    r = c.post(
        f"/api/builds/{bid}/consume",
        json={
            "lines": [
                {
                    "project_entry_id": entry["id"],
                    "part_id": part,
                    "quantity": 10,
                    "storage_location_id": shelf,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text

    r = _kit(c, bid, tray)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "build.kit_error"
    assert "build is complete" in r.json()["status"]["message"]


def test_kit_rolls_back_entirely_when_the_staging_location_is_single_part_only(authed):
    """All-or-nothing on failure: the second part's move violates
    `single_part_only`, and the first part's move is rolled back with it —
    a half-written tray is worse than none."""
    c = authed
    p1 = _create_part(c, "R1")
    p2 = _create_part(c, "R2")
    shelf = _create_storage(c, "Shelf")
    tray = _create_storage(c, "Tray", single_part_only=True)
    _add_stock(c, p1, 100, shelf)
    _add_stock(c, p2, 100, shelf)

    project_id = _project(c, "PCB-SINGLE")
    _add_entry(c, project_id, part_id=p1, quantity=1)
    _add_entry(c, project_id, part_id=p2, quantity=1)
    bid = _build(c, project_id, quantity=5)

    r = _kit(c, bid, tray)
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "stock.constraint_violation"
    assert r.json()["constraint"] == "single_part_only"
    assert _at(c, tray, p1) == 0
    assert _at(c, tray, p2) == 0
    assert _at(c, shelf, p1) == 100
    assert _move_rows(bid) == []


def test_kit_writes_one_audit_row(authed):
    c = authed
    part = _create_part(c, "R4")
    shelf = _create_storage(c, "Shelf")
    tray = _create_storage(c, "Tray")
    _add_stock(c, part, 100, shelf)

    project_id = _project(c, "PCB-AUDIT")
    _add_entry(c, project_id, part_id=part, quantity=3)
    bid = _build(c, project_id, quantity=10)

    assert _kit(c, bid, tray).status_code == 200

    with SessionLocal() as s:
        rows = list(
            s.execute(
                select(AuditLog)
                .where(AuditLog.action == "build.kitted")
                .order_by(AuditLog.created_at.desc())
            ).scalars()
        )
    assert len(rows) == 1
    assert rows[0].target_type == "build"
    assert uuid.UUID(bid) in rows[0].target_ids
    assert "Tray" in rows[0].comment

    # The read-only preview writes no audit row.
    c.get(f"/api/builds/{bid}/kit-plan", params={"storage_location_id": tray})
    with SessionLocal() as s:
        assert (
            s.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "build.kitted")
            ).scalar_one()
            == 1
        )
