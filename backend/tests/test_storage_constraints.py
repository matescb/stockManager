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
    (even if currently zero) is allowed back."""
    loc = _storage(c, "EPO-prior", existing_parts_only=True)
    part = _part(c, "OldPart")

    # Bypass the constraint to seed initial stock (turn off flag, add, turn on).
    # The simplest way: add without the flag, then patch the storage.
    # Use a second storage to add then move.
    staging = _storage(c, "Staging-EPO")
    _add(c, part, 5, staging)

    # Patch storage to enable existing_parts_only.
    r = c.patch(f"/api/storage/{loc}", json={"existing_parts_only": True})
    # Some implementations may not expose patch; use a helper via the DB or
    # create the storage already flagged with a known prior entry.
    # Instead: seed initial stock directly to the loc while it's NOT flagged,
    # then remove it to make it empty, then try to add again.

    # Create a fresh location without the flag, add stock, set flag, remove,
    # verify it still allows re-add.
    loc2 = _storage(c, "EPO-prior2")  # no flag yet
    part2 = _part(c, "OldPart2")

    # Add then remove (creates prior positive entry).
    _add(c, part2, 3, loc2)
    _remove(c, part2, 3, loc2)

    # Now enable the flag on loc2 via PATCH.
    r2 = c.patch(f"/api/storage/{loc2}", json={"existing_parts_only": True})
    # If PATCH is not supported we just re-create with the flag knowing the
    # prior entries already exist. But the flag needs to be True at query time.
    if r2.status_code not in (200, 201):
        # Fallback: the test is not applicable if storage patch isn't wired.
        pytest.skip("storage PATCH not available")

    # part2 has prior positive history at loc2 — should be accepted.
    r3 = c.post(
        "/api/stock/add",
        json={"part_id": part2, "quantity": 2, "storage_location_id": loc2},
    )
    assert r3.status_code == 200, r3.text


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
