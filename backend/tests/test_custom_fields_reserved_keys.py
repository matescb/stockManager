from __future__ import annotations

import uuid

import pytest

from app.domain.custom_fields.models import CustomField


def _create_part(client) -> str:
    r = client.post("/api/parts", json={"name": "Reserved key test", "part_type": "local"})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _workspace_id(client) -> uuid.UUID:
    r = client.get("/api/auth/me")
    assert r.status_code == 200, r.text
    return uuid.UUID(r.json()["data"]["workspaces"][0]["id"])


@pytest.mark.parametrize("key", ["image_url", "datasheet_url", "source_url"])
def test_post_reserved_key_blocked(authed_client, key):
    part_id = _create_part(authed_client)

    r = authed_client.post(
        "/api/custom-fields",
        json={
            "object_type": "part",
            "object_id": part_id,
            "key": key,
            "value": "https://example.com/tracker",
        },
    )

    assert r.status_code == 400, r.text
    body = r.json()
    assert body["status"]["category"] == "validation_error"
    assert body["code"] == "custom_field.reserved_key"
    assert body["key"] == key


def test_override_flip_reserved_key_blocked(authed_client, db):
    part_id = _create_part(authed_client)
    row = CustomField(
        workspace_id=_workspace_id(authed_client),
        object_type="part",
        object_id=uuid.UUID(part_id),
        key="image_url",
        value="/api/parts/assets/ws/original.png",
        source="provider",
    )
    db.add(row)
    db.flush()

    r = authed_client.post(
        "/api/custom-fields",
        json={
            "object_type": "part",
            "object_id": part_id,
            "key": "image_url",
            "value": "https://example.com/tracker.png",
        },
    )

    assert r.status_code == 400, r.text
    db.refresh(row)
    assert row.source == "provider"
    assert row.value == "/api/parts/assets/ws/original.png"
    assert row.original_value is None
