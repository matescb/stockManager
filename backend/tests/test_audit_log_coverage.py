from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select

from app.domain.audit.models import AuditLog


def _create_part(client, name: str) -> str:
    r = client.post("/api/parts", json={"name": name, "part_type": "local"})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _create_storage(client, name: str) -> str:
    r = client.post("/api/storage", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _create_lot(client, part_id: str) -> str:
    r = client.post(
        "/api/stock/add",
        json={
            "part_id": part_id,
            "quantity": 1,
            "lot": {"name": "Audit lot"},
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["lot_id"]


@pytest.mark.parametrize(
    ("route_name", "setup", "method", "path", "body", "action", "target_type"),
    [
        (
            "lots.patch_lot",
            lambda client: _create_lot(client, _create_part(client, "Audit Lot Part")),
            "patch",
            "/api/lots/{target_id}",
            {"comments": "cycle counted"},
            "lot.updated",
            "lot",
        ),
        (
            "storage.patch_storage",
            lambda client: _create_storage(client, "Audit Shelf"),
            "patch",
            "/api/storage/{target_id}",
            {"description": "controlled storage"},
            "storage.updated",
            "storage_location",
        ),
    ],
)
def test_each_mutator_writes_audit_row(
    authed_client,
    db,
    route_name,
    setup,
    method,
    path,
    body,
    action,
    target_type,
):
    target_id = setup(authed_client)

    r = getattr(authed_client, method)(path.format(target_id=target_id), json=body)

    assert r.status_code == 200, f"{route_name}: {r.text}"
    row = db.execute(
        select(AuditLog)
        .where(AuditLog.action == action)
        .where(AuditLog.target_type == target_type)
        .order_by(AuditLog.created_at.desc())
    ).scalar_one()
    assert row.target_ids == [UUID(target_id)]
    assert row.comment == "fields=" + ",".join(sorted(body))
