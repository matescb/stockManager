from __future__ import annotations

import uuid

from app.core.errors import ErrorCodes
from tests._factories import add_stock, create_part, create_storage


def test_flip_blocked_when_multiple_parts(authed_client):
    storage_id = create_storage(authed_client, name=f"Patch-SPO-{uuid.uuid4().hex[:8]}")
    part_a = create_part(authed_client, name=f"Patch-SPO-A-{uuid.uuid4().hex[:8]}")
    part_b = create_part(authed_client, name=f"Patch-SPO-B-{uuid.uuid4().hex[:8]}")
    add_stock(authed_client, part_a, 5, storage_id=storage_id)
    add_stock(authed_client, part_b, 3, storage_id=storage_id)

    response = authed_client.patch(
        f"/api/storage/{storage_id}",
        json={"single_part_only": True},
    )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == ErrorCodes.STORAGE_CONSTRAINT_VIOLATION
    assert body["constraint"] == "single_part_only"
    assert body["storage_location_id"] == storage_id

    after = authed_client.get(f"/api/storage/{storage_id}")
    assert after.status_code == 200, after.text
    assert after.json()["data"]["single_part_only"] is False
