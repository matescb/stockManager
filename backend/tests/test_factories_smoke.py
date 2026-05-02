"""Smoke regression for the shared test factories.

Cheap pin against signature drift in `tests/_factories.py`. If a factory
is renamed or its return shape changes, this test fails before any of
the 30+ call sites do.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app
from tests._factories import (
    add_stock,
    create_part,
    create_project_with_bom,
    create_storage,
    signup_user,
)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (TypeError, ValueError):
        return False
    return True


def test_factories_round_trip_against_real_api():
    c = TestClient(app)
    r = signup_user(c)
    assert r.status_code == 200
    assert _is_uuid(r.json()["data"]["workspace_id"])

    part_id = create_part(c, name="Smoke part")
    assert _is_uuid(part_id)

    storage_id = create_storage(c, name="Smoke bin")
    assert _is_uuid(storage_id)

    add_resp = add_stock(c, part_id, 10, storage_id=storage_id, lot_name="L1")
    assert add_resp.status_code == 200
    entry = add_resp.json()["data"]
    assert _is_uuid(entry["lot_id"])

    project_id = create_project_with_bom(
        c,
        "Smoke project",
        [{"part_id": part_id, "quantity": 1}],
    )
    assert _is_uuid(project_id)
