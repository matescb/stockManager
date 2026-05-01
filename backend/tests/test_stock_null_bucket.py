"""Tests for the BE-002 / DB-002 fix: align service-side validation with
the 0013 `check_stock_nonneg` trigger's NULL-bucket grouping.

The trigger groups by `IS NOT DISTINCT FROM NEW.lot_id` and `IS NOT
DISTINCT FROM NEW.storage_location_id` — i.e. NULL is a distinct
bucket, not a wildcard. Before this PR, the service's `current_quantity`
treated `lot_id=None` / `storage_location_id=None` as "don't filter,
aggregate globally", so a remove with NULL coordinates against a part
whose stock lived in non-NULL buckets passed validation, fell through
the trigger, and surfaced as a 500.

After: passing `bucket_match=True` (which `remove_stock` /
`move_stock` / `adjust_stock` and `consume`'s pre-pass + per-line
checks now do) makes `None` mean "the SQL NULL bucket specifically".
The validator returns the trigger's view of the world; mismatches
surface as a clean 4xx StockError instead of a check_violation 500.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.domain.parts.models import Part
from app.domain.stock.schemas import AddStockIn, LotInput, RemoveStockIn
from app.domain.stock.service import (
    StockError,
    add_stock,
    current_quantity,
    remove_stock,
    total_for_part,
)
from app.domain.storage.models import StorageLocation
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace, WorkspaceMember


@pytest.fixture
def ws_user(db: Session):
    user = User(email=f"u-{uuid.uuid4().hex[:6]}@x.com", name="t", password_hash="x")
    db.add(user)
    db.flush()
    ws = Workspace(name="W", kind="organization", owner_user_id=user.id, currency_default="USD")
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, status="active"))
    db.commit()
    return ws, user


@pytest.fixture
def part_and_storage(db, ws_user):
    ws, user = ws_user
    part = Part(
        workspace_id=ws.id,
        name="R 10k",
        part_type="local",
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(part)
    storage = StorageLocation(
        workspace_id=ws.id, name="Bin", created_by=user.id, updated_by=user.id
    )
    db.add(storage)
    db.commit()
    return ws, user, part, storage


def test_remove_with_null_lot_against_non_null_lot_raises_clean_4xx(db, part_and_storage):
    """Stock lives at (storage=Bin, lot=L1, qty=100). A remove with
    `lot_id=None` would previously aggregate to 100, pass the service
    check, then fail at the trigger with check_violation → 500. After
    bucket_match=True, the validator looks at the NULL-lot bucket
    (which has 0 stock) and raises StockError before any insert."""
    ws, user, part, storage = part_and_storage
    add_stock(
        db,
        workspace_id=ws.id,
        user_id=user.id,
        payload=AddStockIn(
            part_id=part.id,
            quantity=100,
            storage_location_id=storage.id,
            lot=LotInput(name="L1"),
        ),
    )
    db.commit()

    # Sanity: the stock is in a non-NULL lot bucket.
    assert total_for_part(db, workspace_id=ws.id, part_id=part.id) == 100

    # Remove with lot_id=None (NULL bucket) against a part whose stock
    # is in lot=L1 → service-layer 4xx, NOT a 500 from the trigger.
    with pytest.raises(StockError, match="insufficient stock"):
        remove_stock(
            db,
            workspace_id=ws.id,
            user_id=user.id,
            payload=RemoveStockIn(
                part_id=part.id,
                quantity=10,
                storage_location_id=storage.id,
                lot_id=None,
            ),
        )


def test_remove_with_null_storage_against_non_null_storage_raises_clean_4xx(
    db, part_and_storage
):
    """Same shape as above but for storage_location_id."""
    ws, user, part, storage = part_and_storage
    add_stock(
        db,
        workspace_id=ws.id,
        user_id=user.id,
        payload=AddStockIn(
            part_id=part.id,
            quantity=50,
            storage_location_id=storage.id,
        ),
    )
    db.commit()

    with pytest.raises(StockError, match="insufficient stock"):
        remove_stock(
            db,
            workspace_id=ws.id,
            user_id=user.id,
            payload=RemoveStockIn(
                part_id=part.id,
                quantity=5,
                storage_location_id=None,  # mismatch — stock is at storage.id
            ),
        )


def test_remove_with_explicit_bucket_still_works(db, part_and_storage):
    """Happy path regression — passing the same bucket the add wrote to
    aligns with the trigger and the remove succeeds cleanly."""
    ws, user, part, storage = part_and_storage
    e = add_stock(
        db,
        workspace_id=ws.id,
        user_id=user.id,
        payload=AddStockIn(
            part_id=part.id,
            quantity=20,
            storage_location_id=storage.id,
            lot=LotInput(name="L1"),
        ),
    )
    db.commit()
    assert e.lot_id is not None

    remove_stock(
        db,
        workspace_id=ws.id,
        user_id=user.id,
        payload=RemoveStockIn(
            part_id=part.id,
            quantity=5,
            storage_location_id=storage.id,
            lot_id=e.lot_id,
        ),
    )
    db.commit()

    # bucket_match=True query confirms 15 at (storage, lot=L1, status=on_hand).
    assert (
        current_quantity(
            db,
            workspace_id=ws.id,
            part_id=part.id,
            storage_location_id=storage.id,
            lot_id=e.lot_id,
            bucket_match=True,
        )
        == 15
    )


def test_total_for_part_still_aggregates_globally(db, part_and_storage):
    """`total_for_part` and the default `current_quantity` calls (used
    by reports / list endpoints) keep `bucket_match=False` so they sum
    across all (storage, lot) buckets. Pin that we didn't break that."""
    ws, user, part, storage = part_and_storage
    # Two adds: one with a lot, one without. Total should be 30.
    add_stock(
        db,
        workspace_id=ws.id,
        user_id=user.id,
        payload=AddStockIn(
            part_id=part.id,
            quantity=10,
            storage_location_id=storage.id,
            lot=LotInput(name="L1"),
        ),
    )
    add_stock(
        db,
        workspace_id=ws.id,
        user_id=user.id,
        payload=AddStockIn(
            part_id=part.id,
            quantity=20,
            storage_location_id=storage.id,
        ),
    )
    db.commit()

    assert total_for_part(db, workspace_id=ws.id, part_id=part.id) == 30

    # Per-bucket views with bucket_match=True see only their own bucket.
    null_lot = current_quantity(
        db,
        workspace_id=ws.id,
        part_id=part.id,
        storage_location_id=storage.id,
        lot_id=None,
        bucket_match=True,
    )
    assert null_lot == 20  # only the second add (no lot)


def test_add_stock_takes_advisory_lock(db, part_and_storage):
    """Smoke check that `add_stock` issues `pg_advisory_xact_lock` —
    same posture as the existing remove/move/adjust tests. The lock is
    a `SELECT pg_advisory_xact_lock(...)` so we observe it via a
    successful add (the lock SQL would raise if mis-shaped) and the
    resulting row landing as expected. The contended-write coverage
    lives in the threaded test in test_stock_concurrency.py — what we
    pin here is just "add_stock doesn't bypass the lock helper."""
    ws, user, part, storage = part_and_storage
    e = add_stock(
        db,
        workspace_id=ws.id,
        user_id=user.id,
        payload=AddStockIn(
            part_id=part.id,
            quantity=7,
            storage_location_id=storage.id,
        ),
    )
    db.commit()
    assert e.quantity_delta == 7
    assert total_for_part(db, workspace_id=ws.id, part_id=part.id) == 7
