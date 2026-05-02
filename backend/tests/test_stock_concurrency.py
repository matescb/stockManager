"""Tests for the stock TOCTOU + non-negative-balance fix (BE CRIT-1).

Two layers:
1. Service-layer advisory lock keyed on (workspace, part) — serialises
   concurrent writes so the read-then-write race window can't be hit.
2. Database-side AFTER INSERT trigger that re-aggregates the per-tuple
   sum and rolls back if it would go negative — defense in depth.

The trigger is the cleaner test target (deterministic, no concurrency
needed). The advisory lock is verified via a threaded concurrency test
that's mostly a smoke check — the load-bearing guarantee is the trigger.
"""
from __future__ import annotations

import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app


from tests._factories import (
    add_stock as _factory_add_stock,
    create_part as _create_part,
    create_storage as _create_storage,
    signup_user,
)


@pytest.fixture
def authed():
    c = TestClient(app)
    signup_user(c)
    return c


def _add_stock(c, part_id, qty, storage_id, lot_name="L"):
    return _factory_add_stock(
        c, part_id, qty, storage_id=storage_id, lot_name=lot_name
    ).json()["data"]


# ---------------------------------------------------------------------------
# Trigger — direct SQL insert that would push the per-tuple sum negative.
# Bypasses the service layer (no advisory lock acquired) so this purely
# tests the database-side guarantee.
# ---------------------------------------------------------------------------


def test_trigger_blocks_negative_balance_via_direct_sql(authed):
    """Add 100 to a lot via the API, then attempt to write a -150 row
    via raw SQL. The trigger fires on insert and rolls back."""
    part_id = _create_part(authed, "P1")
    storage_id = _create_storage(authed, "B1")
    entry = _add_stock(authed, part_id, 100, storage_id)
    lot_id = entry["lot_id"]
    workspace_id = authed.get("/api/auth/me").json()["data"]["workspaces"][0]["id"]

    from app.infra.db import SessionLocal

    with SessionLocal() as s:
        # Bypass the service layer — write a raw SQL INSERT that would
        # take the (workspace, part, lot, storage, status='on_hand') sum
        # to -50. Trigger should reject.
        with pytest.raises(Exception) as exc_info:
            s.execute(
                text(
                    """
                    INSERT INTO stock_entries
                        (id, workspace_id, part_id, lot_id, storage_location_id,
                         quantity_delta, status, operation_type, occurred_at, created_at)
                    VALUES
                        (:id, :ws, :part, :lot, :sl, -150, 'on_hand', 'remove', NOW(), NOW())
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "ws": workspace_id,
                    "part": part_id,
                    "lot": lot_id,
                    "sl": storage_id,
                },
            )
            s.commit()
        # Trigger raises with our custom message; SQLAlchemy wraps it as
        # IntegrityError. Either the message or check_violation is fine
        # to pin.
        err = str(exc_info.value).lower()
        assert "negative" in err or "check" in err, exc_info.value


def test_trigger_allows_non_negative_balance(authed):
    """Sanity check: a -50 write against a 100-piece lot succeeds via
    raw SQL (with the right tuple). Confirms the trigger isn't
    rejecting legitimate writes."""
    part_id = _create_part(authed, "P2")
    storage_id = _create_storage(authed, "B2")
    entry = _add_stock(authed, part_id, 100, storage_id)
    lot_id = entry["lot_id"]
    workspace_id = authed.get("/api/auth/me").json()["data"]["workspaces"][0]["id"]

    from app.infra.db import SessionLocal

    with SessionLocal() as s:
        s.execute(
            text(
                """
                INSERT INTO stock_entries
                    (id, workspace_id, part_id, lot_id, storage_location_id,
                     quantity_delta, status, operation_type, occurred_at, created_at)
                VALUES
                    (:id, :ws, :part, :lot, :sl, -50, 'on_hand', 'remove', NOW(), NOW())
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "ws": workspace_id,
                "part": part_id,
                "lot": lot_id,
                "sl": storage_id,
            },
        )
        s.commit()

    summary = authed.get(f"/api/parts/{part_id}/stock").json()["data"]
    total = summary["total_on_hand"]
    assert total == 50


# ---------------------------------------------------------------------------
# Advisory lock — concurrent removes from the same lot.
#
# Two threads each fire `POST /api/stock/remove` for 60 of a 100-piece
# lot. Without the lock, both pass `current_quantity >= 60` and both
# write -60 (lot goes to -20). With the lock, the second blocks on
# the first's commit, then re-checks and sees `current_quantity = 40`
# (or its own 100 if the first failed) and returns 400 "insufficient
# stock". Net guarantee: no negative balance regardless of which path
# the two threads end up on.
# ---------------------------------------------------------------------------


def test_concurrent_removes_cannot_both_drain_below_zero(authed):
    part_id = _create_part(authed, "P3")
    storage_id = _create_storage(authed, "B3")
    entry = _add_stock(authed, part_id, 100, storage_id)
    lot_id = entry["lot_id"]

    # Each thread uses its own TestClient instance to get an independent
    # SQLAlchemy connection (TestClient per-call wraps a new ASGI loop).
    cookie = next(c for c in authed.cookies.jar)

    results: list[int] = []
    barrier = threading.Barrier(2)

    def do_remove():
        c = TestClient(app)
        c.cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path)
        barrier.wait()  # both threads ready before either fires
        r = c.post(
            "/api/stock/remove",
            json={
                "part_id": part_id,
                "quantity": 60,
                "storage_location_id": storage_id,
                "lot_id": lot_id,
            },
        )
        results.append(r.status_code)

    t1 = threading.Thread(target=do_remove)
    t2 = threading.Thread(target=do_remove)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    # Whatever combination of (200, 400) or (200, 500) we end up with,
    # the load-bearing assertion is: the lot's net quantity is never
    # negative. Could be 40 (one succeeded), or 100 (one failed before
    # write, retry exhausted).
    assert len(results) == 2
    successes = sum(1 for s in results if s in (200, 201))
    assert successes <= 1, f"both removes succeeded: {results}"

    summary = authed.get(f"/api/parts/{part_id}/stock").json()["data"]
    total = summary["total_on_hand"]
    assert total >= 0, f"lot went negative: {total}; results={results}"
    # And specifically: either 40 (one removed) or 100 (none removed).
    assert total in (40, 100), f"unexpected total {total}; results={results}"
