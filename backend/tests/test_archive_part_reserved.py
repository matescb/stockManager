from __future__ import annotations

import uuid

from app.domain.stock.models import StockEntry
from tests._factories import create_part


def _current_workspace_id(client) -> uuid.UUID:
    response = client.get("/api/workspaces/current")
    assert response.status_code == 200, response.text
    return uuid.UUID(response.json()["data"]["id"])


def _add_reserved_entry(db, *, workspace_id: uuid.UUID, part_id: uuid.UUID, quantity: int) -> None:
    db.add(
        StockEntry(
            workspace_id=workspace_id,
            part_id=part_id,
            quantity_delta=quantity,
            status="reserved",
            operation_type="reserve" if quantity > 0 else "release_reservation",
        )
    )
    db.flush()


def test_archive_part_blocked_when_reserved_stock_present(authed_client, db):
    part_id = create_part(authed_client, "Reserved IC")
    workspace_id = _current_workspace_id(authed_client)

    _add_reserved_entry(
        db,
        workspace_id=workspace_id,
        part_id=uuid.UUID(part_id),
        quantity=6,
    )

    response = authed_client.post(f"/api/parts/{part_id}/archive")

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "part.has_reserved_stock"
    assert body["status"]["category"] == "conflict"
    assert body["blocking"] == [{"part_id": part_id, "quantity": 6}]

    current = authed_client.get(f"/api/parts/{part_id}")
    assert current.status_code == 200, current.text
    assert current.json()["data"]["archived_at"] is None


def test_archive_part_succeeds_after_reserved_stock_released(authed_client, db):
    part_id = create_part(authed_client, "Released Reservation IC")
    workspace_id = _current_workspace_id(authed_client)

    _add_reserved_entry(
        db,
        workspace_id=workspace_id,
        part_id=uuid.UUID(part_id),
        quantity=6,
    )
    _add_reserved_entry(
        db,
        workspace_id=workspace_id,
        part_id=uuid.UUID(part_id),
        quantity=-6,
    )

    response = authed_client.post(f"/api/parts/{part_id}/archive")

    assert response.status_code == 200, response.text
    current = authed_client.get(f"/api/parts/{part_id}")
    assert current.status_code == 200, current.text
    assert current.json()["data"]["archived_at"] is not None
