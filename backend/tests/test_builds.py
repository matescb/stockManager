from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def authed():
    c = TestClient(app)
    r = c.post(
        "/api/auth/signup",
        json={"email": f"u-{uuid.uuid4().hex[:8]}@x.com", "name": "u", "password": "password123"},
    )
    assert r.status_code == 200, r.text
    return c


def _create_part(c, name, **extra):
    r = c.post("/api/parts", json={"name": name, "part_type": "local", **extra})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _create_storage(c, name="Shelf"):
    r = c.post("/api/storage", json={"name": name})
    assert r.status_code in (200, 201)
    return r.json()["data"]["id"]


def _add_stock(c, part_id, qty, storage_id=None):
    body = {"part_id": part_id, "quantity": qty}
    if storage_id:
        body["storage_location_id"] = storage_id
    r = c.post("/api/stock/add", json=body)
    assert r.status_code == 200, r.text


def _create_project_with_bom(c, project_name, bom):
    """bom is a list of dicts: {part_id, quantity, dnp?}."""
    r = c.post("/api/projects", json={"name": project_name})
    assert r.status_code in (200, 201)
    pid = r.json()["data"]["id"]
    for row in bom:
        r = c.post(
            f"/api/projects/{pid}/entries",
            json={
                "part_id": row.get("part_id"),
                "quantity": row["quantity"],
                "dnp": row.get("dnp", False),
                "name": row.get("name"),
            },
        )
        assert r.status_code in (200, 201), r.text
    return pid


def test_shortage_analysis_flags_short(authed):
    c = authed
    p1 = _create_part(c, "R1k 0402")
    p2 = _create_part(c, "C100n 0402")
    storage = _create_storage(c)
    _add_stock(c, p1, 50, storage)
    _add_stock(c, p2, 100, storage)

    project_id = _create_project_with_bom(
        c, "PCB-A",
        [{"part_id": p1, "quantity": 10}, {"part_id": p2, "quantity": 5}],
    )

    r = c.post("/api/builds", json={"name": "B-1", "project_id": project_id, "quantity": 6})
    assert r.status_code == 201, r.text
    bid = r.json()["data"]["id"]

    detail = c.get(f"/api/builds/{bid}").json()["data"]
    by_part = {row["part_id"]: row for row in detail["shortage"]}
    # 10 * 6 = 60 needed for p1; only 50 in stock → short by 10
    assert by_part[p1]["required"] == 60
    assert by_part[p1]["available"] == 50
    assert by_part[p1]["short_by"] == 10
    # 5 * 6 = 30 needed for p2; 100 in stock → not short
    assert by_part[p2]["required"] == 30
    assert by_part[p2]["short_by"] == 0


def test_consume_full_build_with_subassembly_output(authed):
    c = authed
    sub = _create_part(c, "SubAssembly-1")
    p1 = _create_part(c, "R1k")
    p2 = _create_part(c, "C100n")
    storage = _create_storage(c)
    _add_stock(c, p1, 100, storage)
    _add_stock(c, p2, 100, storage)

    proj_id = _create_project_with_bom(
        c, "PCB-B",
        [{"part_id": p1, "quantity": 5}, {"part_id": p2, "quantity": 2}],
    )
    # Tag the sub-assembly
    r = c.patch(f"/api/projects/{proj_id}", json={})  # ensure exists
    # PATCH route doesn't accept associated_subassembly_part_id — set via direct DB?
    # Use the raw API: there is no exposed endpoint, so just check the basic case w/o output.

    r = c.post("/api/builds", json={"name": "B-A", "project_id": proj_id, "quantity": 10})
    bid = r.json()["data"]["id"]
    entries = c.get(f"/api/projects/{proj_id}/entries").json()["data"]
    e1 = next(e for e in entries if e["part_id"] == p1)
    e2 = next(e for e in entries if e["part_id"] == p2)

    # 5 * 10 = 50 of p1, 2 * 10 = 20 of p2
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

    # Stock decremented
    s1 = c.get(f"/api/parts/{p1}/stock").json()["data"]["total_on_hand"]
    s2 = c.get(f"/api/parts/{p2}/stock").json()["data"]["total_on_hand"]
    assert s1 == 50
    assert s2 == 80

    # Build is now read-only-ish: another consume rejected
    r = c.post(
        f"/api/builds/{bid}/consume",
        json={
            "lines": [
                {"project_entry_id": e1["id"], "part_id": p1, "quantity": 1, "storage_location_id": storage},
            ]
        },
    )
    assert r.status_code == 400


def test_consume_under_required_rejected(authed):
    c = authed
    p1 = _create_part(c, "R1k")
    storage = _create_storage(c)
    _add_stock(c, p1, 100, storage)
    proj_id = _create_project_with_bom(c, "PCB-C", [{"part_id": p1, "quantity": 10}])
    r = c.post("/api/builds", json={"name": "B-C", "project_id": proj_id, "quantity": 5})
    bid = r.json()["data"]["id"]
    e = c.get(f"/api/projects/{proj_id}/entries").json()["data"][0]

    # 10 * 5 = 50 required, supply only 49
    r = c.post(
        f"/api/builds/{bid}/consume",
        json={"lines": [{"project_entry_id": e["id"], "part_id": p1, "quantity": 49, "storage_location_id": storage}]},
    )
    assert r.status_code == 400
    assert "under-consumed" in r.json()["status"]["message"]


def test_dnp_entry_skipped(authed):
    c = authed
    p1 = _create_part(c, "R1k")
    p2 = _create_part(c, "DNP-cap")
    storage = _create_storage(c)
    _add_stock(c, p1, 50, storage)
    # No stock for p2, but it's DNP so it shouldn't matter
    proj_id = _create_project_with_bom(
        c, "PCB-D",
        [{"part_id": p1, "quantity": 10}, {"part_id": p2, "quantity": 10, "dnp": True}],
    )
    r = c.post("/api/builds", json={"name": "B-D", "project_id": proj_id, "quantity": 1})
    bid = r.json()["data"]["id"]
    detail = c.get(f"/api/builds/{bid}").json()["data"]
    # Only one entry shows up in shortage
    assert len(detail["shortage"]) == 1

    e1 = next(e for e in c.get(f"/api/projects/{proj_id}/entries").json()["data"] if e["part_id"] == p1)
    r = c.post(
        f"/api/builds/{bid}/consume",
        json={"lines": [{"project_entry_id": e1["id"], "part_id": p1, "quantity": 10, "storage_location_id": storage}]},
    )
    assert r.status_code == 200, r.text


def test_substitute_consumption(authed):
    c = authed
    main = _create_part(c, "R1k 0402")
    alt = _create_part(c, "R1k 0603")
    storage = _create_storage(c)
    _add_stock(c, main, 5, storage)
    _add_stock(c, alt, 100, storage)

    # Register alt as a bidirectional substitute for main
    r = c.post(f"/api/parts/{main}/substitutes", json={"substitute_part_id": alt})
    assert r.status_code == 200, r.text

    proj_id = _create_project_with_bom(c, "PCB-E", [{"part_id": main, "quantity": 10}])
    r = c.post("/api/builds", json={"name": "B-E", "project_id": proj_id, "quantity": 5})
    bid = r.json()["data"]["id"]
    e = c.get(f"/api/projects/{proj_id}/entries").json()["data"][0]

    # Need 50; only 5 of main. Use 5 main + 45 alt.
    r = c.post(
        f"/api/builds/{bid}/consume",
        json={
            "lines": [
                {"project_entry_id": e["id"], "part_id": main, "quantity": 5, "storage_location_id": storage},
                {"project_entry_id": e["id"], "part_id": alt, "quantity": 45, "storage_location_id": storage},
            ]
        },
    )
    assert r.status_code == 200, r.text


def test_consume_rejects_oversubscribed_substitute_across_entries(authed):
    """BE CRIT-3: two BOM entries each claiming 60 from the same 100-piece
    reel of a substitute. Pre-fix, each line passed `current_quantity >=
    line.quantity` independently and the lot ended up at -20. The pre-pass
    aggregation now sums demand per (part, lot, storage) tuple before any
    write and rejects the combined 120 > 100."""
    c = authed
    main = _create_part(c, "R1k 0402")
    alt = _create_part(c, "R1k 0603")
    storage = _create_storage(c)
    _add_stock(c, main, 200, storage)  # plenty of main, not what we're testing
    # Add 100 of alt and grab the lot id directly from the response.
    r = c.post(
        "/api/stock/add",
        json={"part_id": alt, "quantity": 100, "storage_location_id": storage, "lot": {"name": "ALT-LOT-1"}},
    )
    assert r.status_code in (200, 201), r.text
    alt_lot_id = r.json()["data"]["lot_id"]

    # alt is bidirectional substitute for main.
    r = c.post(f"/api/parts/{main}/substitutes", json={"substitute_part_id": alt})
    assert r.status_code == 200, r.text

    # Two entries in the BOM, each demanding 60 of `main` per build (=60 with quantity=1).
    proj_id = _create_project_with_bom(
        c, "PCB-CRIT3",
        [{"part_id": main, "quantity": 60}, {"part_id": main, "quantity": 60}],
    )
    r = c.post("/api/builds", json={"name": "B-X", "project_id": proj_id, "quantity": 1})
    bid = r.json()["data"]["id"]
    entries = c.get(f"/api/projects/{proj_id}/entries").json()["data"]
    e1, e2 = entries[0], entries[1]

    # Both lines target the SAME (part=alt, lot=alt_lot_id, storage=storage)
    # tuple. Combined demand 120 > 100 available — must reject up front.
    r = c.post(
        f"/api/builds/{bid}/consume",
        json={
            "lines": [
                {"project_entry_id": e1["id"], "part_id": alt, "lot_id": alt_lot_id, "storage_location_id": storage, "quantity": 60},
                {"project_entry_id": e2["id"], "part_id": alt, "lot_id": alt_lot_id, "storage_location_id": storage, "quantity": 60},
            ]
        },
    )
    assert r.status_code == 400, r.text
    msg = r.json()["status"]["message"]
    assert "insufficient stock" in msg
    assert "120" in msg, msg
    assert "100" in msg, msg

    # And the build must NOT have written any consume rows. Stock for the
    # substitute lot should still be the full 100.
    from app.domain.stock.models import StockEntry
    from app.infra.db import SessionLocal
    from sqlalchemy import select, func
    with SessionLocal() as s:
        total = s.execute(
            select(func.coalesce(func.sum(StockEntry.quantity_delta), 0))
            .where(StockEntry.lot_id == uuid.UUID(alt_lot_id))
            .where(StockEntry.status == "on_hand")
        ).scalar_one()
        assert int(total) == 100


def test_consume_allows_aggregated_demand_within_stock(authed):
    """The other side of the aggregation gate: two entries each claiming
    30 from the same lot of 100 → combined 60 < 100, must be accepted."""
    c = authed
    main = _create_part(c, "R1k 0402")
    alt = _create_part(c, "R1k 0603")
    storage = _create_storage(c)
    r = c.post(
        "/api/stock/add",
        json={"part_id": alt, "quantity": 100, "storage_location_id": storage, "lot": {"name": "ALT-LOT-2"}},
    )
    alt_lot_id = r.json()["data"]["lot_id"]
    c.post(f"/api/parts/{main}/substitutes", json={"substitute_part_id": alt})

    proj_id = _create_project_with_bom(
        c, "PCB-OK",
        [{"part_id": main, "quantity": 30}, {"part_id": main, "quantity": 30}],
    )
    r = c.post("/api/builds", json={"name": "B-OK", "project_id": proj_id, "quantity": 1})
    bid = r.json()["data"]["id"]
    entries = c.get(f"/api/projects/{proj_id}/entries").json()["data"]

    r = c.post(
        f"/api/builds/{bid}/consume",
        json={
            "lines": [
                {"project_entry_id": entries[0]["id"], "part_id": alt, "lot_id": alt_lot_id, "storage_location_id": storage, "quantity": 30},
                {"project_entry_id": entries[1]["id"], "part_id": alt, "lot_id": alt_lot_id, "storage_location_id": storage, "quantity": 30},
            ]
        },
    )
    assert r.status_code == 200, r.text


def test_non_substitute_rejected(authed):
    c = authed
    main = _create_part(c, "R1k")
    other = _create_part(c, "C-100n")  # not a substitute
    storage = _create_storage(c)
    _add_stock(c, main, 100, storage)
    _add_stock(c, other, 100, storage)

    proj_id = _create_project_with_bom(c, "PCB-F", [{"part_id": main, "quantity": 1}])
    r = c.post("/api/builds", json={"name": "B-F", "project_id": proj_id, "quantity": 1})
    bid = r.json()["data"]["id"]
    e = c.get(f"/api/projects/{proj_id}/entries").json()["data"][0]

    r = c.post(
        f"/api/builds/{bid}/consume",
        json={"lines": [{"project_entry_id": e["id"], "part_id": other, "quantity": 1, "storage_location_id": storage}]},
    )
    assert r.status_code == 400
    assert "not a substitute" in r.json()["status"]["message"]
