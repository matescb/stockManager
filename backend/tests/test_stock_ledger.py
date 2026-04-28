"""Spec §21.1 + §21.2 — stock ledger and lot split invariants."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.domain.parts.models import Part
from app.domain.stock.schemas import (
    AddStockIn,
    AdjustStockIn,
    MoveStockIn,
    PriceInput,
    LotInput,
    RemoveStockIn,
)
from app.domain.stock.service import (
    add_stock,
    adjust_stock,
    current_quantity,
    move_stock,
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
    part = Part(workspace_id=ws.id, name="Cap 0.1uF", part_type="local", created_by=user.id, updated_by=user.id)
    db.add(part)
    a = StorageLocation(workspace_id=ws.id, name="Shelf A", created_by=user.id, updated_by=user.id)
    b = StorageLocation(workspace_id=ws.id, name="Shelf B", created_by=user.id, updated_by=user.id)
    db.add_all([a, b])
    db.commit()
    return ws, user, part, a, b


def test_stock_ledger_smoke(db, part_and_storage):
    ws, user, part, a, b = part_and_storage
    add_stock(
        db, workspace_id=ws.id, user_id=user.id,
        payload=AddStockIn(part_id=part.id, quantity=100, storage_location_id=a.id),
    )
    db.commit()
    assert total_for_part(db, workspace_id=ws.id, part_id=part.id) == 100
    assert current_quantity(db, workspace_id=ws.id, part_id=part.id, storage_location_id=a.id) == 100

    move_stock(
        db, workspace_id=ws.id, user_id=user.id,
        payload=MoveStockIn(
            part_id=part.id,
            source_storage_location_id=a.id,
            destination_storage_location_id=b.id,
            quantity=25,
        ),
    )
    db.commit()
    assert current_quantity(db, workspace_id=ws.id, part_id=part.id, storage_location_id=a.id) == 75
    assert current_quantity(db, workspace_id=ws.id, part_id=part.id, storage_location_id=b.id) == 25
    assert total_for_part(db, workspace_id=ws.id, part_id=part.id) == 100

    remove_stock(
        db, workspace_id=ws.id, user_id=user.id,
        payload=RemoveStockIn(part_id=part.id, quantity=10, storage_location_id=b.id),
    )
    db.commit()
    assert current_quantity(db, workspace_id=ws.id, part_id=part.id, storage_location_id=b.id) == 15
    assert total_for_part(db, workspace_id=ws.id, part_id=part.id) == 90


def test_lot_split(db, part_and_storage):
    ws, user, part, a, b = part_and_storage
    e = add_stock(
        db, workspace_id=ws.id, user_id=user.id,
        payload=AddStockIn(
            part_id=part.id,
            quantity=1000,
            storage_location_id=a.id,
            price=PriceInput(mode="per_component", unit_price=0.01, currency="USD"),
            lot=LotInput(name="L1"),
        ),
    )
    db.commit()
    src_lot_id = e.lot_id
    assert src_lot_id is not None

    move_stock(
        db, workspace_id=ws.id, user_id=user.id,
        payload=MoveStockIn(
            part_id=part.id,
            source_storage_location_id=a.id,
            source_lot_id=src_lot_id,
            destination_storage_location_id=b.id,
            quantity=250,
            split_lot=True,
        ),
    )
    db.commit()

    # source lot should have 750 in shelf A
    assert current_quantity(db, workspace_id=ws.id, part_id=part.id, lot_id=src_lot_id) == 750

    # find new (split) lot
    from app.domain.lots.models import Lot
    new_lots = [l for l in db.query(Lot).filter(Lot.workspace_id == ws.id).all() if l.parent_lot_id == src_lot_id]
    assert len(new_lots) == 1
    new_lot = new_lots[0]
    assert current_quantity(db, workspace_id=ws.id, part_id=part.id, lot_id=new_lot.id) == 250
    assert total_for_part(db, workspace_id=ws.id, part_id=part.id) == 1000


def test_remove_more_than_available_fails(db, part_and_storage):
    from app.domain.stock.service import StockError
    ws, user, part, a, _ = part_and_storage
    add_stock(
        db, workspace_id=ws.id, user_id=user.id,
        payload=AddStockIn(part_id=part.id, quantity=5, storage_location_id=a.id),
    )
    db.commit()
    with pytest.raises(StockError):
        remove_stock(
            db, workspace_id=ws.id, user_id=user.id,
            payload=RemoveStockIn(part_id=part.id, quantity=10, storage_location_id=a.id),
        )


def test_adjust_writes_delta(db, part_and_storage):
    ws, user, part, a, _ = part_and_storage
    add_stock(
        db, workspace_id=ws.id, user_id=user.id,
        payload=AddStockIn(part_id=part.id, quantity=20, storage_location_id=a.id),
    )
    db.commit()
    e = adjust_stock(
        db, workspace_id=ws.id, user_id=user.id,
        payload=AdjustStockIn(part_id=part.id, storage_location_id=a.id, actual_quantity=18),
    )
    db.commit()
    assert e is not None
    assert e.quantity_delta == -2
    assert current_quantity(db, workspace_id=ws.id, part_id=part.id, storage_location_id=a.id) == 18
