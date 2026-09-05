"""Multi-stage builds (Track B2).

Covers the five properties the feature has to hold:

* per-stage requirement maths goes through `_required`, so both attrition
  sources compound into the staged numbers too;
* stage portions that sum to 100% consume exactly what a single-pass build
  consumes (cumulative allocation, no rounding drift);
* reservations are taken once, up front, for the whole build and released
  slice-by-slice — never double-counted per stage;
* a build with no stages behaves exactly as it did before this feature;
* stages are workspace-scoped like every other table.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.domain.stock.models import StockEntry
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
    """Create a project + BOM entries, returning (project_id, entries).

    Unlike `tests._factories.create_project_with_bom` this forwards every
    field (notably `attrition_pct`) so the attrition-compounding cases can
    set it.
    """
    r = c.post("/api/projects", json={"name": name})
    assert r.status_code in (200, 201), r.text
    pid = r.json()["data"]["id"]
    for row in rows:
        r = c.post(f"/api/projects/{pid}/entries", json=row)
        assert r.status_code in (200, 201), r.text
    entries = c.get(f"/api/projects/{pid}/entries").json()["data"]
    return pid, entries


def _reserved_total(part_id: str) -> int:
    with SessionLocal() as s:
        return int(
            s.execute(
                select(func.coalesce(func.sum(StockEntry.quantity_delta), 0))
                .where(StockEntry.part_id == uuid.UUID(part_id))
                .where(StockEntry.status == "reserved")
            ).scalar_one()
        )


def _stage_consume_rows(stage_id: str) -> list[StockEntry]:
    with SessionLocal() as s:
        return list(
            s.execute(
                select(StockEntry)
                .where(StockEntry.build_stage_id == uuid.UUID(stage_id))
                .where(StockEntry.operation_type == "build_consume")
            ).scalars()
        )


# --- Backwards compatibility -------------------------------------------------


def test_build_without_stages_is_unchanged(authed):
    """Regression net for the single-pass path: a build with no stages must
    behave exactly as it did before multi-stage builds existed — reserve on
    create, consume the whole BOM in one call, complete, decrement stock."""
    c = authed
    p1 = _create_part(c, "R1k")
    p2 = _create_part(c, "C100n")
    storage = _create_storage(c)
    _add_stock(c, p1, 100, storage)
    _add_stock(c, p2, 100, storage)

    pid, entries = _project_with_entries(
        c,
        "PCB-single-pass",
        [{"part_id": p1, "quantity": 5}, {"part_id": p2, "quantity": 2}],
    )
    r = c.post("/api/builds", json={"name": "B-single", "project_id": pid, "quantity": 10})
    assert r.status_code == 201, r.text
    bid = r.json()["data"]["id"]

    # Reservations were written up front for the whole build.
    assert _reserved_total(p1) == 50
    assert _reserved_total(p2) == 20

    # The stage list is empty and the whole-build consume endpoint still works.
    assert c.get(f"/api/builds/{bid}/stages").json()["data"] == []

    e1 = next(e for e in entries if e["part_id"] == p1)
    e2 = next(e for e in entries if e["part_id"] == p2)
    r = c.post(
        f"/api/builds/{bid}/consume",
        json={
            "lines": [
                {"project_entry_id": e1["id"], "part_id": p1, "quantity": 50, "storage_location_id": storage},
                {"project_entry_id": e2["id"], "part_id": p2, "quantity": 20, "storage_location_id": storage},
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "complete"

    assert c.get(f"/api/parts/{p1}/stock").json()["data"]["total_on_hand"] == 50
    assert c.get(f"/api/parts/{p2}/stock").json()["data"]["total_on_hand"] == 80
    # Reservations fully released, and no ledger row carries a stage.
    assert _reserved_total(p1) == 0
    assert _reserved_total(p2) == 0
    with SessionLocal() as s:
        staged = s.execute(
            select(func.count(StockEntry.id)).where(StockEntry.build_stage_id.is_not(None))
        ).scalar_one()
    assert staged == 0


def test_whole_build_consume_refused_once_stages_exist(authed):
    c = authed
    p1 = _create_part(c, "R1k")
    storage = _create_storage(c)
    _add_stock(c, p1, 100, storage)
    pid, entries = _project_with_entries(c, "PCB-guard", [{"part_id": p1, "quantity": 10}])
    bid = c.post(
        "/api/builds", json={"name": "B-guard", "project_id": pid, "quantity": 1}
    ).json()["data"]["id"]

    r = c.post(
        f"/api/builds/{bid}/stages",
        json={"name": "Stage 1", "lines": [{"project_entry_id": entries[0]["id"]}]},
    )
    assert r.status_code == 201, r.text

    r = c.post(
        f"/api/builds/{bid}/consume",
        json={
            "lines": [
                {"project_entry_id": entries[0]["id"], "part_id": p1, "quantity": 10, "storage_location_id": storage}
            ]
        },
    )
    assert r.status_code == 400
    assert r.json()["code"] == "build.has_stages"


# --- Per-stage requirement maths --------------------------------------------


def test_stage_requirements_split_by_portion(authed):
    c = authed
    p1 = _create_part(c, "R1k")
    storage = _create_storage(c)
    _add_stock(c, p1, 200, storage)
    pid, entries = _project_with_entries(c, "PCB-split", [{"part_id": p1, "quantity": 10}])
    entry_id = entries[0]["id"]
    bid = c.post(
        "/api/builds", json={"name": "B-split", "project_id": pid, "quantity": 10}
    ).json()["data"]["id"]

    # 10 * 10 = 100 required for the whole build; 40% / 60% across two stages.
    r = c.post(
        f"/api/builds/{bid}/stages",
        json={"name": "SMT", "lines": [{"project_entry_id": entry_id, "portion_pct": 40}]},
    )
    assert r.status_code == 201, r.text
    r = c.post(
        f"/api/builds/{bid}/stages",
        json={"name": "THT", "lines": [{"project_entry_id": entry_id, "portion_pct": 60}]},
    )
    assert r.status_code == 201, r.text

    stages = c.get(f"/api/builds/{bid}/stages").json()["data"]
    assert [s["name"] for s in stages] == ["SMT", "THT"]
    assert [s["sequence"] for s in stages] == [0, 1]
    assert stages[0]["shortage"][0]["required"] == 40
    assert stages[1]["shortage"][0]["required"] == 60
    assert stages[0]["shortage"][0]["portion_pct"] == 40.0


def test_stage_requirements_compound_both_attrition_sources(authed):
    """The staged requirement must be a slice of `_required(...)`, not of the
    raw BOM quantity — so part-intrinsic attrition and the per-BOM-line
    `attrition_pct` still compound, and the slices still sum to the whole."""
    c = authed
    # Part-intrinsic 10% attrition.
    p1 = _create_part(c, "R1k-lossy", attrition_percentage=10)
    storage = _create_storage(c)
    _add_stock(c, p1, 500, storage)
    # Per-BOM-line 25% attrition on top.
    pid, entries = _project_with_entries(
        c, "PCB-attrition", [{"part_id": p1, "quantity": 100, "attrition_pct": 25}]
    )
    entry_id = entries[0]["id"]
    bid = c.post(
        "/api/builds", json={"name": "B-attr", "project_id": pid, "quantity": 1}
    ).json()["data"]["id"]

    # 100 * 1.10 * 1.25 = 137.5 -> ceil 138 for the whole build.
    detail = c.get(f"/api/builds/{bid}").json()["data"]
    assert detail["shortage"][0]["required"] == 138

    for name, pct in (("A", 50), ("B", 50)):
        r = c.post(
            f"/api/builds/{bid}/stages",
            json={"name": name, "lines": [{"project_entry_id": entry_id, "portion_pct": pct}]},
        )
        assert r.status_code == 201, r.text

    stages = c.get(f"/api/builds/{bid}/stages").json()["data"]
    required = [s["shortage"][0]["required"] for s in stages]
    # ceil(138 * 0.5) = 69 then 138 - 69 = 69. Sums to the whole-build 138 —
    # never 2 * ceil(69) = 140 (invented stock) nor 2 * floor = 136 (lost).
    assert required == [69, 69]
    assert sum(required) == 138
    # The attrition the UI shows is still the BOM line's own rate.
    assert stages[0]["shortage"][0]["attrition_pct"] == 25.0


def test_odd_split_allocation_sums_to_whole_build(authed):
    """Cumulative-ceiling allocation: thirds of an odd requirement must still
    add up to the single-pass total."""
    c = authed
    p1 = _create_part(c, "R1k-odd")
    storage = _create_storage(c)
    _add_stock(c, p1, 200, storage)
    pid, entries = _project_with_entries(c, "PCB-thirds", [{"part_id": p1, "quantity": 103}])
    entry_id = entries[0]["id"]
    bid = c.post(
        "/api/builds", json={"name": "B-thirds", "project_id": pid, "quantity": 1}
    ).json()["data"]["id"]

    for name, pct in (("A", "33.3333"), ("B", "33.3333"), ("C", "33.3334")):
        r = c.post(
            f"/api/builds/{bid}/stages",
            json={"name": name, "lines": [{"project_entry_id": entry_id, "portion_pct": pct}]},
        )
        assert r.status_code == 201, r.text

    stages = c.get(f"/api/builds/{bid}/stages").json()["data"]
    required = [s["shortage"][0]["required"] for s in stages]
    assert sum(required) == 103, required


def test_stage_over_commit_rejected(authed):
    c = authed
    p1 = _create_part(c, "R1k")
    pid, entries = _project_with_entries(c, "PCB-over", [{"part_id": p1, "quantity": 10}])
    entry_id = entries[0]["id"]
    bid = c.post(
        "/api/builds", json={"name": "B-over", "project_id": pid, "quantity": 1}
    ).json()["data"]["id"]

    r = c.post(
        f"/api/builds/{bid}/stages",
        json={"name": "A", "lines": [{"project_entry_id": entry_id, "portion_pct": 60}]},
    )
    assert r.status_code == 201, r.text
    r = c.post(
        f"/api/builds/{bid}/stages",
        json={"name": "B", "lines": [{"project_entry_id": entry_id, "portion_pct": 60}]},
    )
    assert r.status_code == 400
    assert "over-committed" in r.json()["status"]["message"]


def test_stage_rejects_dnp_entry(authed):
    c = authed
    p1 = _create_part(c, "R1k")
    p2 = _create_part(c, "DNP-cap")
    pid, entries = _project_with_entries(
        c,
        "PCB-dnp-stage",
        [{"part_id": p1, "quantity": 1}, {"part_id": p2, "quantity": 1, "dnp": True}],
    )
    dnp_entry = next(e for e in entries if e["part_id"] == p2)
    bid = c.post(
        "/api/builds", json={"name": "B-dnp", "project_id": pid, "quantity": 1}
    ).json()["data"]["id"]

    r = c.post(
        f"/api/builds/{bid}/stages",
        json={"name": "A", "lines": [{"project_entry_id": dnp_entry["id"]}]},
    )
    assert r.status_code == 400
    assert "DNP" in r.json()["status"]["message"]


# --- Per-stage consume -------------------------------------------------------


def test_staged_consume_draws_stock_progressively(authed):
    c = authed
    p1 = _create_part(c, "R1k")
    p2 = _create_part(c, "C100n")
    storage = _create_storage(c)
    _add_stock(c, p1, 100, storage)
    _add_stock(c, p2, 100, storage)
    pid, entries = _project_with_entries(
        c,
        "PCB-progressive",
        [{"part_id": p1, "quantity": 10}, {"part_id": p2, "quantity": 4}],
    )
    e1 = next(e for e in entries if e["part_id"] == p1)
    e2 = next(e for e in entries if e["part_id"] == p2)
    bid = c.post(
        "/api/builds", json={"name": "B-prog", "project_id": pid, "quantity": 5}
    ).json()["data"]["id"]

    # Stage 1 takes all of the resistors; stage 2 takes all of the caps.
    s1 = c.post(
        f"/api/builds/{bid}/stages",
        json={"name": "SMT", "lines": [{"project_entry_id": e1["id"]}]},
    ).json()["data"]
    s2 = c.post(
        f"/api/builds/{bid}/stages",
        json={"name": "THT", "lines": [{"project_entry_id": e2["id"]}]},
    ).json()["data"]

    # Stage 1: 10 * 5 = 50 resistors.
    r = c.post(
        f"/api/builds/{bid}/stages/{s1['id']}/consume",
        json={
            "lines": [
                {"project_entry_id": e1["id"], "part_id": p1, "quantity": 50, "storage_location_id": storage}
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["stage_status"] == "complete"
    assert body["build_status"] == "in_progress"
    assert body["remaining_stages"] == 1

    # Only the resistors moved. The device is half built.
    assert c.get(f"/api/parts/{p1}/stock").json()["data"]["total_on_hand"] == 50
    assert c.get(f"/api/parts/{p2}/stock").json()["data"]["total_on_hand"] == 100

    # The stage's own ledger rows are tagged with the stage.
    rows = _stage_consume_rows(s1["id"])
    assert len(rows) == 1
    assert rows[0].quantity_delta == -50
    assert _stage_consume_rows(s2["id"]) == []

    # Stage 2: 4 * 5 = 20 caps. Completes the build.
    r = c.post(
        f"/api/builds/{bid}/stages/{s2['id']}/consume",
        json={
            "lines": [
                {"project_entry_id": e2["id"], "part_id": p2, "quantity": 20, "storage_location_id": storage}
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["build_status"] == "complete"
    assert c.get(f"/api/parts/{p2}/stock").json()["data"]["total_on_hand"] == 80
    assert c.get(f"/api/builds/{bid}").json()["data"]["build"]["status"] == "complete"


def test_staged_consume_matches_single_pass_totals(authed):
    """Two 50% stages of the same BOM must draw exactly the stock a
    single-pass build of the same quantity draws — no more, no less."""
    c = authed
    p1 = _create_part(c, "R1k-half", attrition_percentage=10)
    storage = _create_storage(c)
    _add_stock(c, p1, 400, storage)
    pid, entries = _project_with_entries(
        c, "PCB-halves", [{"part_id": p1, "quantity": 100, "attrition_pct": 25}]
    )
    entry_id = entries[0]["id"]
    bid = c.post(
        "/api/builds", json={"name": "B-halves", "project_id": pid, "quantity": 1}
    ).json()["data"]["id"]

    stages = []
    for name in ("A", "B"):
        stages.append(
            c.post(
                f"/api/builds/{bid}/stages",
                json={
                    "name": name,
                    "lines": [{"project_entry_id": entry_id, "portion_pct": 50}],
                },
            ).json()["data"]
        )

    before = c.get(f"/api/parts/{p1}/stock").json()["data"]["total_on_hand"]
    for stage in stages:
        required = stage["shortage"][0]["required"]
        r = c.post(
            f"/api/builds/{bid}/stages/{stage['id']}/consume",
            json={
                "lines": [
                    {
                        "project_entry_id": entry_id,
                        "part_id": p1,
                        "quantity": required,
                        "storage_location_id": storage,
                    }
                ]
            },
        )
        assert r.status_code == 200, r.text
    after = c.get(f"/api/parts/{p1}/stock").json()["data"]["total_on_hand"]
    # 100 * 1.10 * 1.25 = 137.5 -> 138, exactly what one pass would take.
    assert before - after == 138


def test_stage_under_consume_rejected(authed):
    c = authed
    p1 = _create_part(c, "R1k")
    storage = _create_storage(c)
    _add_stock(c, p1, 100, storage)
    pid, entries = _project_with_entries(c, "PCB-under", [{"part_id": p1, "quantity": 10}])
    entry_id = entries[0]["id"]
    bid = c.post(
        "/api/builds", json={"name": "B-under", "project_id": pid, "quantity": 1}
    ).json()["data"]["id"]
    stage = c.post(
        f"/api/builds/{bid}/stages",
        json={"name": "A", "lines": [{"project_entry_id": entry_id, "portion_pct": 50}]},
    ).json()["data"]

    # Stage requires 5; supply 4.
    r = c.post(
        f"/api/builds/{bid}/stages/{stage['id']}/consume",
        json={
            "lines": [
                {"project_entry_id": entry_id, "part_id": p1, "quantity": 4, "storage_location_id": storage}
            ]
        },
    )
    assert r.status_code == 400
    assert "under-consumed" in r.json()["status"]["message"]


def test_stage_rejects_line_outside_the_stage(authed):
    c = authed
    p1 = _create_part(c, "R1k")
    p2 = _create_part(c, "C100n")
    storage = _create_storage(c)
    _add_stock(c, p1, 100, storage)
    _add_stock(c, p2, 100, storage)
    pid, entries = _project_with_entries(
        c, "PCB-outside", [{"part_id": p1, "quantity": 5}, {"part_id": p2, "quantity": 5}]
    )
    e1 = next(e for e in entries if e["part_id"] == p1)
    e2 = next(e for e in entries if e["part_id"] == p2)
    bid = c.post(
        "/api/builds", json={"name": "B-outside", "project_id": pid, "quantity": 1}
    ).json()["data"]["id"]
    stage = c.post(
        f"/api/builds/{bid}/stages",
        json={"name": "A", "lines": [{"project_entry_id": e1["id"]}]},
    ).json()["data"]

    r = c.post(
        f"/api/builds/{bid}/stages/{stage['id']}/consume",
        json={
            "lines": [
                {"project_entry_id": e1["id"], "part_id": p1, "quantity": 5, "storage_location_id": storage},
                {"project_entry_id": e2["id"], "part_id": p2, "quantity": 5, "storage_location_id": storage},
            ]
        },
    )
    assert r.status_code == 400
    assert "not in this stage" in r.json()["status"]["message"]
    # Nothing was drawn — the whole stage consume is all-or-nothing.
    assert c.get(f"/api/parts/{p1}/stock").json()["data"]["total_on_hand"] == 100


def test_stages_must_be_consumed_in_sequence(authed):
    c = authed
    p1 = _create_part(c, "R1k")
    storage = _create_storage(c)
    _add_stock(c, p1, 100, storage)
    pid, entries = _project_with_entries(c, "PCB-order", [{"part_id": p1, "quantity": 10}])
    entry_id = entries[0]["id"]
    bid = c.post(
        "/api/builds", json={"name": "B-order", "project_id": pid, "quantity": 1}
    ).json()["data"]["id"]
    c.post(
        f"/api/builds/{bid}/stages",
        json={"name": "First", "lines": [{"project_entry_id": entry_id, "portion_pct": 50}]},
    )
    second = c.post(
        f"/api/builds/{bid}/stages",
        json={"name": "Second", "lines": [{"project_entry_id": entry_id, "portion_pct": 50}]},
    ).json()["data"]

    r = c.post(
        f"/api/builds/{bid}/stages/{second['id']}/consume",
        json={
            "lines": [
                {"project_entry_id": entry_id, "part_id": p1, "quantity": 5, "storage_location_id": storage}
            ]
        },
    )
    assert r.status_code == 400
    assert "must be consumed before" in r.json()["status"]["message"]


def test_stage_consume_is_not_repeatable(authed):
    c = authed
    p1 = _create_part(c, "R1k")
    storage = _create_storage(c)
    _add_stock(c, p1, 100, storage)
    pid, entries = _project_with_entries(c, "PCB-once", [{"part_id": p1, "quantity": 10}])
    entry_id = entries[0]["id"]
    bid = c.post(
        "/api/builds", json={"name": "B-once", "project_id": pid, "quantity": 1}
    ).json()["data"]["id"]
    # Two stages so replaying stage A hits the stage guard, not the
    # "build is complete" guard the last stage would trip.
    stage = c.post(
        f"/api/builds/{bid}/stages",
        json={"name": "A", "lines": [{"project_entry_id": entry_id, "portion_pct": 50}]},
    ).json()["data"]
    c.post(
        f"/api/builds/{bid}/stages",
        json={"name": "B", "lines": [{"project_entry_id": entry_id, "portion_pct": 50}]},
    )
    body = {
        "lines": [
            {"project_entry_id": entry_id, "part_id": p1, "quantity": 5, "storage_location_id": storage}
        ]
    }
    assert c.post(f"/api/builds/{bid}/stages/{stage['id']}/consume", json=body).status_code == 200
    r = c.post(f"/api/builds/{bid}/stages/{stage['id']}/consume", json=body)
    assert r.status_code == 400
    assert "already complete" in r.json()["status"]["message"]
    assert c.get(f"/api/parts/{p1}/stock").json()["data"]["total_on_hand"] == 95


# --- Reservations ------------------------------------------------------------


def test_reservations_are_up_front_and_not_double_counted(authed):
    """Reservations are taken ONCE at build creation for the whole build.
    Adding stages must not write more reserve rows, and each stage consume
    must release only its own slice — leaving the later stages reserved."""
    c = authed
    p1 = _create_part(c, "R1k")
    storage = _create_storage(c)
    _add_stock(c, p1, 100, storage)
    pid, entries = _project_with_entries(c, "PCB-reserve", [{"part_id": p1, "quantity": 10}])
    entry_id = entries[0]["id"]
    bid = c.post(
        "/api/builds", json={"name": "B-reserve", "project_id": pid, "quantity": 4}
    ).json()["data"]["id"]

    # 10 * 4 = 40 reserved for the whole build.
    assert _reserved_total(p1) == 40

    stages = []
    for name in ("A", "B"):
        stages.append(
            c.post(
                f"/api/builds/{bid}/stages",
                json={
                    "name": name,
                    "lines": [{"project_entry_id": entry_id, "portion_pct": 50}],
                },
            ).json()["data"]
        )
    # Creating stages writes NO extra reservation — 80 here would be the
    # double-count this test exists to prevent.
    assert _reserved_total(p1) == 40

    r = c.post(
        f"/api/builds/{bid}/stages/{stages[0]['id']}/consume",
        json={
            "lines": [
                {"project_entry_id": entry_id, "part_id": p1, "quantity": 20, "storage_location_id": storage}
            ]
        },
    )
    assert r.status_code == 200, r.text
    # Half consumed, half still reserved for stage B.
    assert _reserved_total(p1) == 20

    r = c.post(
        f"/api/builds/{bid}/stages/{stages[1]['id']}/consume",
        json={
            "lines": [
                {"project_entry_id": entry_id, "part_id": p1, "quantity": 20, "storage_location_id": storage}
            ]
        },
    )
    assert r.status_code == 200, r.text
    # Build complete — reservation fully drained, never negative.
    assert _reserved_total(p1) == 0
    assert c.get(f"/api/parts/{p1}/stock").json()["data"]["total_on_hand"] == 60


def test_partial_stage_coverage_releases_remainder_on_completion(authed):
    """Stages covering only 60% of a line leave 40% of the reservation
    outstanding; completing the last stage must free it rather than leaking
    a permanent reservation."""
    c = authed
    p1 = _create_part(c, "R1k")
    storage = _create_storage(c)
    _add_stock(c, p1, 100, storage)
    pid, entries = _project_with_entries(c, "PCB-partial", [{"part_id": p1, "quantity": 10}])
    entry_id = entries[0]["id"]
    bid = c.post(
        "/api/builds", json={"name": "B-partial", "project_id": pid, "quantity": 1}
    ).json()["data"]["id"]
    assert _reserved_total(p1) == 10

    stage = c.post(
        f"/api/builds/{bid}/stages",
        json={"name": "Only", "lines": [{"project_entry_id": entry_id, "portion_pct": 60}]},
    ).json()["data"]
    assert stage["shortage"][0]["required"] == 6

    r = c.post(
        f"/api/builds/{bid}/stages/{stage['id']}/consume",
        json={
            "lines": [
                {"project_entry_id": entry_id, "part_id": p1, "quantity": 6, "storage_location_id": storage}
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["build_status"] == "complete"
    assert _reserved_total(p1) == 0
    assert c.get(f"/api/parts/{p1}/stock").json()["data"]["total_on_hand"] == 94


def test_archive_after_partial_stage_release_does_not_over_release(authed):
    """The release accounting is quantity-based: archiving a build whose
    first stage already released half must free only the other half, never
    drive the reserved total negative."""
    c = authed
    p1 = _create_part(c, "R1k")
    storage = _create_storage(c)
    _add_stock(c, p1, 100, storage)
    pid, entries = _project_with_entries(c, "PCB-archive", [{"part_id": p1, "quantity": 10}])
    entry_id = entries[0]["id"]
    bid = c.post(
        "/api/builds", json={"name": "B-archive", "project_id": pid, "quantity": 2}
    ).json()["data"]["id"]
    assert _reserved_total(p1) == 20

    stages = [
        c.post(
            f"/api/builds/{bid}/stages",
            json={"name": name, "lines": [{"project_entry_id": entry_id, "portion_pct": 50}]},
        ).json()["data"]
        for name in ("A", "B")
    ]
    r = c.post(
        f"/api/builds/{bid}/stages/{stages[0]['id']}/consume",
        json={
            "lines": [
                {"project_entry_id": entry_id, "part_id": p1, "quantity": 10, "storage_location_id": storage}
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert _reserved_total(p1) == 10

    assert c.post(f"/api/builds/{bid}/archive").status_code == 200
    assert _reserved_total(p1) == 0


def test_partial_release_accounting_does_not_truncate_fractions(authed):
    """`stock_entries.quantity_delta` is `Numeric(18,6)` since alembic 0074.

    The partial-release accounting reads outstanding reserve quantities off
    that column; an `int()` there would truncate a fraction the column can
    now physically hold and silently under-release. No API path writes a
    fractional quantity yet, so this test writes one straight to the ledger
    and asserts the release counters it exactly rather than dropping the
    0.5. Guards the same coercion 0074 had to remove from `_required`.
    """
    from decimal import Decimal

    from app.domain.builds.models import Build
    from app.domain.builds.service import _outstanding_reservations

    c = authed
    p1 = _create_part(c, "R1k-frac")
    storage = _create_storage(c)
    _add_stock(c, p1, 100, storage)
    pid, entries = _project_with_entries(c, "PCB-frac", [{"part_id": p1, "quantity": 10}])
    bid = c.post(
        "/api/builds", json={"name": "B-frac", "project_id": pid, "quantity": 1}
    ).json()["data"]["id"]

    with SessionLocal() as s:
        reserve = s.execute(
            select(StockEntry)
            .where(StockEntry.build_id == uuid.UUID(bid))
            .where(StockEntry.operation_type == "reserve")
        ).scalar_one()
        # Reserve 10.5 and release 0.25 of it: 10.25 must remain outstanding.
        reserve.quantity_delta = Decimal("10.5")
        s.add(
            StockEntry(
                workspace_id=reserve.workspace_id,
                part_id=reserve.part_id,
                quantity_delta=Decimal("-0.25"),
                status="reserved",
                operation_type="release",
                related_entry_id=reserve.id,
                build_id=reserve.build_id,
                project_id=reserve.project_id,
            )
        )
        s.flush()

        build = s.get(Build, uuid.UUID(bid))
        _part_ids, outstanding = _outstanding_reservations(
            s, workspace_id=build.workspace_id, build=build
        )
        assert len(outstanding) == 1
        # int() truncation would give 10 (10 - 0) instead of 10.25.
        assert outstanding[0][1] == Decimal("10.25")


def test_quantity_change_refused_after_a_stage_consumed(authed):
    c = authed
    p1 = _create_part(c, "R1k")
    storage = _create_storage(c)
    _add_stock(c, p1, 100, storage)
    pid, entries = _project_with_entries(c, "PCB-qty", [{"part_id": p1, "quantity": 10}])
    entry_id = entries[0]["id"]
    bid = c.post(
        "/api/builds", json={"name": "B-qty", "project_id": pid, "quantity": 2}
    ).json()["data"]["id"]
    stages = [
        c.post(
            f"/api/builds/{bid}/stages",
            json={"name": name, "lines": [{"project_entry_id": entry_id, "portion_pct": 50}]},
        ).json()["data"]
        for name in ("A", "B")
    ]
    c.post(
        f"/api/builds/{bid}/stages/{stages[0]['id']}/consume",
        json={
            "lines": [
                {"project_entry_id": entry_id, "part_id": p1, "quantity": 10, "storage_location_id": storage}
            ]
        },
    )
    r = c.patch(f"/api/builds/{bid}", json={"quantity": 5})
    assert r.status_code == 400
    assert r.json()["code"] == "build.read_only"
    # Renaming is still fine.
    assert c.patch(f"/api/builds/{bid}", json={"name": "renamed"}).status_code == 200


# --- Sub-assembly output -----------------------------------------------------


def test_output_lot_produced_once_on_the_final_stage(authed):
    c = authed
    sub = _create_part(c, "SubAssembly-staged")
    p1 = _create_part(c, "R1k")
    storage = _create_storage(c)
    _add_stock(c, p1, 100, storage)
    pid, entries = _project_with_entries(c, "PCB-output", [{"part_id": p1, "quantity": 10}])
    entry_id = entries[0]["id"]
    assert c.patch(f"/api/projects/{pid}", json={"associated_subassembly_part_id": sub}).status_code == 200
    bid = c.post(
        "/api/builds", json={"name": "B-output", "project_id": pid, "quantity": 3}
    ).json()["data"]["id"]

    stages = [
        c.post(
            f"/api/builds/{bid}/stages",
            json={"name": name, "lines": [{"project_entry_id": entry_id, "portion_pct": 50}]},
        ).json()["data"]
        for name in ("A", "B")
    ]

    r = c.post(
        f"/api/builds/{bid}/stages/{stages[0]['id']}/consume",
        json={
            "lines": [
                {"project_entry_id": entry_id, "part_id": p1, "quantity": 15, "storage_location_id": storage}
            ]
        },
    )
    assert r.status_code == 200, r.text
    # No output while the device is only half built.
    assert r.json()["data"]["output_lot_id"] is None
    assert c.get(f"/api/parts/{sub}/stock").json()["data"]["total_on_hand"] == 0

    r = c.post(
        f"/api/builds/{bid}/stages/{stages[1]['id']}/consume",
        json={
            "lines": [
                {"project_entry_id": entry_id, "part_id": p1, "quantity": 15, "storage_location_id": storage}
            ],
            "output_lot_name": "staged-out",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["output_lot_id"] is not None
    # Exactly one unit-batch of output, not one per stage.
    assert c.get(f"/api/parts/{sub}/stock").json()["data"]["total_on_hand"] == 3
    assert c.get(f"/api/builds/{bid}").json()["data"]["build"]["output_lot_id"] is not None


# --- Audit -------------------------------------------------------------------


def test_stage_mutations_write_audit_rows(authed):
    c = authed
    p1 = _create_part(c, "R1k")
    storage = _create_storage(c)
    _add_stock(c, p1, 100, storage)
    pid, entries = _project_with_entries(c, "PCB-audit", [{"part_id": p1, "quantity": 10}])
    entry_id = entries[0]["id"]
    bid = c.post(
        "/api/builds", json={"name": "B-audit", "project_id": pid, "quantity": 1}
    ).json()["data"]["id"]
    stage = c.post(
        f"/api/builds/{bid}/stages",
        json={"name": "A", "lines": [{"project_entry_id": entry_id}]},
    ).json()["data"]
    c.post(
        f"/api/builds/{bid}/stages/{stage['id']}/consume",
        json={
            "lines": [
                {"project_entry_id": entry_id, "part_id": p1, "quantity": 10, "storage_location_id": storage}
            ]
        },
    )

    rows = c.get("/api/audit").json()["data"]
    actions = [row["action"] for row in rows]
    assert "build_stage.created" in actions
    assert "build_stage.consumed" in actions
    created = next(row for row in rows if row["action"] == "build_stage.created")
    assert created["target_type"] == "build_stage"
    assert stage["id"] in created["target_ids"]
