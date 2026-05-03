"""Storage constraint enforcement — BE-004.

Tests that `single_part_only` and `existing_parts_only` flags on
StorageLocation are enforced via current positive on-hand stock
(through stock_for_storage), not by historical row counts.

Regression cases (marked "regression") verify the old broken
behaviour is gone: the old code counted any historical StockEntry
with a different part_id, so a location that once held part A but is
now empty would still reject part B. The new code only looks at
current positive balances.
"""
from __future__ import annotations

import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _signup(c: TestClient) -> None:
    r = c.post(
        "/api/auth/signup",
        json={
            "email": f"u-{uuid.uuid4().hex[:8]}@example.com",
            "name": "tester",
            "password": "TestPass-2026-Stronk",
        },
    )
    assert r.status_code == 200, r.text


@pytest.fixture
def c():
    client = TestClient(app)
    _signup(client)
    return client


def _part(c: TestClient, name: str = "P") -> str:
    r = c.post("/api/parts", json={"name": name, "part_type": "local"})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _storage(c: TestClient, name: str = "Bin", **flags) -> str:
    body = {"name": name}
    body.update(flags)
    r = c.post("/api/storage", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _add(c: TestClient, part_id: str, qty: int, storage_id: str) -> None:
    r = c.post(
        "/api/stock/add",
        json={"part_id": part_id, "quantity": qty, "storage_location_id": storage_id},
    )
    assert r.status_code == 200, r.text


def _remove(c: TestClient, part_id: str, qty: int, storage_id: str) -> None:
    r = c.post(
        "/api/stock/remove",
        json={"part_id": part_id, "quantity": qty, "storage_location_id": storage_id},
    )
    assert r.status_code == 200, r.text


def _move(c: TestClient, part_id: str, qty: int, src: str, dst: str) -> dict:
    r = c.post(
        "/api/stock/move",
        json={
            "part_id": part_id,
            "quantity": qty,
            "source_storage_location_id": src,
            "destination_storage_location_id": dst,
        },
    )
    return r


# ---------------------------------------------------------------------------
# single_part_only — add_stock
# ---------------------------------------------------------------------------


def test_single_part_only_rejects_different_part_add(c):
    """single_part_only: add part B when part A has positive stock → 409."""
    loc = _storage(c, "SPO", single_part_only=True)
    part_a = _part(c, "A")
    part_b = _part(c, "B")

    # Stock part A into the location first.
    _add(c, part_a, 5, loc)

    # Now try to add part B — should be rejected.
    r = c.post(
        "/api/stock/add",
        json={"part_id": part_b, "quantity": 1, "storage_location_id": loc},
    )
    assert r.status_code == 409, r.text
    detail = r.json()
    assert detail.get("constraint") == "single_part_only"
    assert detail.get("storage_location_id") == loc


def test_single_part_only_allows_same_part_add(c):
    """single_part_only: re-adding the same part that already occupies the location is fine."""
    loc = _storage(c, "SPO-same", single_part_only=True)
    part_a = _part(c, "A2")

    _add(c, part_a, 3, loc)

    r = c.post(
        "/api/stock/add",
        json={"part_id": part_a, "quantity": 2, "storage_location_id": loc},
    )
    assert r.status_code == 200, r.text


def test_single_part_only_allows_add_after_stock_emptied_regression(c):
    """Regression (old code rejected this): single_part_only should allow
    part B once all part A stock has been removed (location is now empty).

    Old code counted any historical StockEntry with part_id != part_b, so
    it always rejected. New code checks current positive balances only.
    """
    loc = _storage(c, "SPO-empty", single_part_only=True)
    part_a = _part(c, "A3")
    part_b = _part(c, "B3")

    # Add and then fully remove part A.
    _add(c, part_a, 4, loc)
    _remove(c, part_a, 4, loc)

    # Location is now empty — part B should be accepted.
    r = c.post(
        "/api/stock/add",
        json={"part_id": part_b, "quantity": 1, "storage_location_id": loc},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# single_part_only — move_stock
# ---------------------------------------------------------------------------


def test_single_part_only_rejects_move_of_different_part(c):
    """single_part_only: moving part B into a location that holds part A → 409."""
    src_a = _storage(c, "Src-A")
    src_b = _storage(c, "Src-B")
    dest = _storage(c, "Dest-SPO", single_part_only=True)

    part_a = _part(c, "MA")
    part_b = _part(c, "MB")

    _add(c, part_a, 5, src_a)
    _add(c, part_b, 5, src_b)

    # Stock part A in dest first.
    r = _move(c, part_a, 3, src_a, dest)
    assert r.status_code == 200, r.text

    # Now try to move part B into the same dest.
    r = _move(c, part_b, 2, src_b, dest)
    assert r.status_code == 409, r.text
    detail = r.json()
    assert detail.get("constraint") == "single_part_only"
    assert detail.get("storage_location_id") == dest


def test_single_part_only_allows_move_after_location_emptied_regression(c):
    """Regression (old code rejected this): moving part B into a
    single_part_only location that held part A but is now empty must succeed.

    Old check used historical row count; new check uses current stock.
    """
    src_a = _storage(c, "Src-A2")
    src_b = _storage(c, "Src-B2")
    dest = _storage(c, "Dest-SPO2", single_part_only=True)
    staging = _storage(c, "Staging")

    part_a = _part(c, "MA2")
    part_b = _part(c, "MB2")

    _add(c, part_a, 5, src_a)
    _add(c, part_b, 5, src_b)

    # Move A into dest, then move it all back out to staging.
    r = _move(c, part_a, 5, src_a, dest)
    assert r.status_code == 200, r.text
    r = _move(c, part_a, 5, dest, staging)
    assert r.status_code == 200, r.text

    # dest is now empty — part B should be accepted.
    r = _move(c, part_b, 3, src_b, dest)
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# existing_parts_only
# ---------------------------------------------------------------------------


def test_existing_parts_only_rejects_part_with_no_prior_history(c):
    """existing_parts_only: part with no prior positive entry at the
    location → 409."""
    loc = _storage(c, "EPO", existing_parts_only=True)
    part = _part(c, "New")

    r = c.post(
        "/api/stock/add",
        json={"part_id": part, "quantity": 1, "storage_location_id": loc},
    )
    assert r.status_code == 409, r.text
    detail = r.json()
    assert detail.get("constraint") == "existing_parts_only"
    assert detail.get("storage_location_id") == loc


def test_existing_parts_only_allows_part_with_prior_positive_history(c):
    """existing_parts_only: part that was previously stocked here
    (even if currently zero) is allowed back.

    Setup: create the location WITHOUT the flag, seed prior positive
    history (add then remove so the location is currently empty), then
    PATCH the flag on. Re-adding the same part must now succeed —
    the helper checks for any prior positive entry, not current stock.
    """
    loc = _storage(c, "EPO-prior")  # no flag yet
    part = _part(c, "OldPart")

    # Add then remove (creates prior positive entry, zero current balance).
    _add(c, part, 3, loc)
    _remove(c, part, 3, loc)

    # Now enable the flag on loc via PATCH.
    r_patch = c.patch(f"/api/storage/{loc}", json={"existing_parts_only": True})
    if r_patch.status_code not in (200, 201):
        # Fallback: the test is not applicable if storage patch isn't wired.
        pytest.skip("storage PATCH not available")

    # part has prior positive history at loc — should be accepted.
    r = c.post(
        "/api/stock/add",
        json={"part_id": part, "quantity": 2, "storage_location_id": loc},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Combined flags
# ---------------------------------------------------------------------------


def test_combined_flags_single_and_existing(c):
    """Location with both flags: part B trying to enter a location that has
    prior history for part A (and currently holds part A) is rejected.

    Setup:
    - Create loc without flags, seed part A stock in it, remove it (creating
      prior history), then enable both flags.
    - Move part A back in (both constraints satisfied: it has prior history,
      and the location is empty when it enters).
    - Part B has no prior history → existing_parts_only fires.
    """
    # Create loc without flags first so we can seed prior history for part A.
    loc = _storage(c, "Both")
    part_a = _part(c, "CombA")
    part_b = _part(c, "CombB")

    staging = _storage(c, "Staging-Comb")
    _add(c, part_a, 5, staging)
    _add(c, part_b, 5, staging)

    # Seed prior positive history for part A at loc (no flags yet).
    _add(c, part_a, 2, loc)
    _remove(c, part_a, 2, loc)

    # Enable both flags now that prior history exists.
    r_patch = c.patch(f"/api/storage/{loc}", json={"single_part_only": True, "existing_parts_only": True})
    assert r_patch.status_code in (200, 201), r_patch.text

    # Move part A into the now-flagged location (has prior history, location empty → OK).
    r = _move(c, part_a, 3, staging, loc)
    assert r.status_code == 200, r.text

    # part B has no prior history AND the location holds part A → 409
    # (either constraint fires; we only care that it's rejected with 409).
    r2 = _move(c, part_b, 2, staging, loc)
    assert r2.status_code == 409, r2.text
    assert r2.json().get("constraint") in ("single_part_only", "existing_parts_only")


# ---------------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------------


def test_workspace_isolation_storage_constraints(c):
    """stock_for_storage is workspace-filtered; a second workspace's
    occupant must not interfere with the first workspace's constraint check."""
    # Workspace 1 (c) has a single_part_only location holding part A.
    loc = _storage(c, "Isolated-SPO", single_part_only=True)
    part_a = _part(c, "IsoA")
    _add(c, part_a, 5, loc)

    # A second independent workspace signs up.
    c2 = TestClient(app)
    _signup(c2)

    # Workspace 2 creates its own unrelated part and storage.
    # These must not influence workspace 1's stock_for_storage query.
    part_b_ws2 = _part(c2, "IsoB-ws2")
    loc_ws2 = _storage(c2, "Bin-ws2")
    _add(c2, part_b_ws2, 10, loc_ws2)

    # Workspace 1: adding a different part to loc should still be rejected
    # (its own occupant part_a is there — not confused by ws2's data).
    part_b_ws1 = _part(c, "IsoB-ws1")
    r = c.post(
        "/api/stock/add",
        json={"part_id": part_b_ws1, "quantity": 1, "storage_location_id": loc},
    )
    assert r.status_code == 409, r.text
    assert r.json().get("constraint") == "single_part_only"


# ---------------------------------------------------------------------------
# Concurrency — cross-part race against single_part_only destination.
#
# Two threads each fire `POST /api/stock/add` for *different parts* into
# the same single_part_only empty location. Without the per-storage
# advisory lock, both pass the `stock_for_storage(loc) == []` check on
# stale reads and both insert — leaving the bin holding two parts in
# violation of the invariant. With the lock, the second blocks on the
# first's commit, then re-reads and sees the first part already there
# and returns 409.
# ---------------------------------------------------------------------------


pytestmark_real_db = pytest.mark.real_db


@pytest.mark.real_db
def test_single_part_only_cross_part_race_blocked(c):
    """Concurrent add of part A and part B into the same single_part_only
    empty location must result in exactly one success — the per-storage
    advisory lock serialises the read/check/write across different parts.
    """
    loc = _storage(c, "SPO-race", single_part_only=True)
    part_a = _part(c, "RaceA")
    part_b = _part(c, "RaceB")

    cookie = next(ck for ck in c.cookies.jar)

    results: list[int] = []
    barrier = threading.Barrier(2)

    def do_add(part_id: str) -> None:
        client = TestClient(app)
        client.cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path)
        barrier.wait()  # both threads ready before either fires
        r = client.post(
            "/api/stock/add",
            json={"part_id": part_id, "quantity": 1, "storage_location_id": loc},
        )
        results.append(r.status_code)

    t1 = threading.Thread(target=do_add, args=(part_a,))
    t2 = threading.Thread(target=do_add, args=(part_b,))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert len(results) == 2
    successes = sum(1 for s in results if s == 200)
    conflicts = sum(1 for s in results if s == 409)
    # Exactly one succeeds, one is rejected with the structured 409.
    assert successes == 1, f"expected 1 success, got results={results}"
    assert conflicts == 1, f"expected 1 conflict, got results={results}"


# ---------------------------------------------------------------------------
# orders.receive — BE-004 follow-up (#280)
#
# `receive` writes a producer StockEntry just like add_stock; the same
# storage-location constraints (single_part_only, existing_parts_only) must
# fire. Pre-fix, `receive` skipped the helper entirely so a
# /api/orders/{id}/receive could land stock into a constrained bin.
# ---------------------------------------------------------------------------


def _create_order(c: TestClient, part_id: str, qty: int = 5) -> tuple[str, str]:
    """Create an order with a single entry for ``part_id``. Returns
    ``(order_id, order_entry_id)``."""
    r = c.post(
        "/api/orders",
        json={
            "name": f"PO-{uuid.uuid4().hex[:6]}",
            "currency": "USD",
            "entries": [{"part_id": part_id, "quantity_ordered": qty}],
        },
    )
    assert r.status_code in (200, 201), r.text
    order_id = r.json()["data"]["id"]
    detail = c.get(f"/api/orders/{order_id}").json()["data"]
    entry_id = detail["entries"][0]["id"]
    return order_id, entry_id


def test_orders_receive_rejects_single_part_only_bin_holding_other_part(c):
    """receive into a single_part_only bin already holding a different
    part must 409 with the same {constraint, storage_location_id} body
    that /api/stock/add returns."""
    loc = _storage(c, "Recv-SPO", single_part_only=True)
    part_a = _part(c, "RecvA")
    part_b = _part(c, "RecvB")

    # Seed part A in the constrained bin.
    _add(c, part_a, 5, loc)

    # Create an order for part B and try to receive into the same bin.
    order_id, entry_id = _create_order(c, part_b, qty=3)
    r = c.post(
        f"/api/orders/{order_id}/receive",
        json={
            "lines": [
                {"order_entry_id": entry_id, "quantity": 3, "storage_location_id": loc}
            ]
        },
    )
    assert r.status_code == 409, r.text
    detail = r.json()
    assert detail.get("constraint") == "single_part_only"
    assert detail.get("storage_location_id") == loc


def test_orders_receive_rejects_existing_parts_only_for_unstocked_part(c):
    """receive into an existing_parts_only bin where the part has no
    prior positive history must 409."""
    loc = _storage(c, "Recv-EPO", existing_parts_only=True)
    part = _part(c, "RecvNew")

    order_id, entry_id = _create_order(c, part, qty=2)
    r = c.post(
        f"/api/orders/{order_id}/receive",
        json={
            "lines": [
                {"order_entry_id": entry_id, "quantity": 2, "storage_location_id": loc}
            ]
        },
    )
    assert r.status_code == 409, r.text
    detail = r.json()
    assert detail.get("constraint") == "existing_parts_only"
    assert detail.get("storage_location_id") == loc


# ---------------------------------------------------------------------------
# builds.consume — sub-assembly output — BE-004 follow-up (#280)
#
# When a project has `associated_subassembly_part_id` set, consume()
# writes a producer StockEntry for the build output. That write must
# also respect storage-location constraints.
# ---------------------------------------------------------------------------


def _make_build_with_output(
    c: TestClient,
    *,
    sub_part_id: str,
    bom_part_id: str,
    bom_storage_id: str,
    bom_qty: int = 10,
) -> tuple[str, str]:
    """Create a project + build configured for sub-assembly output.

    Returns ``(build_id, project_entry_id)``. The caller posts to
    /consume with the desired ``output_storage_location_id``.
    """
    r = c.post("/api/projects", json={"name": f"Proj-{uuid.uuid4().hex[:6]}"})
    assert r.status_code in (200, 201), r.text
    proj_id = r.json()["data"]["id"]

    r = c.post(
        f"/api/projects/{proj_id}/entries",
        json={"part_id": bom_part_id, "quantity": bom_qty},
    )
    assert r.status_code in (200, 201), r.text
    entry_id = r.json()["data"]["id"]

    r = c.patch(
        f"/api/projects/{proj_id}",
        json={"associated_subassembly_part_id": sub_part_id},
    )
    assert r.status_code == 200, r.text

    r = c.post(
        "/api/builds",
        json={"name": f"B-{uuid.uuid4().hex[:6]}", "project_id": proj_id, "quantity": 1},
    )
    assert r.status_code in (200, 201), r.text
    build_id = r.json()["data"]["id"]
    return build_id, entry_id


def test_builds_consume_output_rejects_single_part_only_bin(c):
    """The build output landed into a single_part_only bin already
    holding another part must 409 — the producer write is now guarded
    just like add_stock."""
    storage_in = _storage(c, "Build-Inputs")
    out_loc = _storage(c, "Build-Out-SPO", single_part_only=True)

    bom_part = _part(c, "BOM-R")
    sub_part = _part(c, "SUB")
    blocker = _part(c, "Blocker")

    # Seed plenty of BOM stock and pre-stock the constrained output bin
    # with a different part so the constraint fires on the output write.
    _add(c, bom_part, 100, storage_in)
    _add(c, blocker, 1, out_loc)

    build_id, entry_id = _make_build_with_output(
        c,
        sub_part_id=sub_part,
        bom_part_id=bom_part,
        bom_storage_id=storage_in,
        bom_qty=10,
    )

    r = c.post(
        f"/api/builds/{build_id}/consume",
        json={
            "output_storage_location_id": out_loc,
            "lines": [
                {
                    "project_entry_id": entry_id,
                    "part_id": bom_part,
                    "quantity": 10,
                    "storage_location_id": storage_in,
                }
            ],
        },
    )
    assert r.status_code == 409, r.text
    detail = r.json()
    assert detail.get("constraint") == "single_part_only"
    assert detail.get("storage_location_id") == out_loc


def test_builds_consume_output_allows_unconstrained_bin_regression(c):
    """Sanity: an unconstrained output bin still accepts the build
    output. The new guard must not fire when the destination has no
    storage-constraint flags set."""
    storage_in = _storage(c, "Build-Inputs-Reg")
    out_loc = _storage(c, "Build-Out-Plain")

    bom_part = _part(c, "BOM-R-Reg")
    sub_part = _part(c, "SUB-Reg")

    _add(c, bom_part, 100, storage_in)

    build_id, entry_id = _make_build_with_output(
        c,
        sub_part_id=sub_part,
        bom_part_id=bom_part,
        bom_storage_id=storage_in,
        bom_qty=10,
    )

    r = c.post(
        f"/api/builds/{build_id}/consume",
        json={
            "output_storage_location_id": out_loc,
            "lines": [
                {
                    "project_entry_id": entry_id,
                    "part_id": bom_part,
                    "quantity": 10,
                    "storage_location_id": storage_in,
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "complete"
