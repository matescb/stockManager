"""`custom_fields.provider` / `.value_num` — alembic 0081.

Both columns are additive and nobody writes them yet (A3 does). What has
to hold today is that the schema really carries them, that an exact
`Numeric(36,18)` survives a round trip through the database, and that the
REST surface exposes both without disturbing the existing keys.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.domain.custom_fields.models import CustomField


def _make_part(client: TestClient) -> str:
    return client.post(
        "/api/parts", json={"name": "R 10k", "part_type": "local"}
    ).json()["data"]["id"]


def test_new_columns_default_to_null_and_are_serialised(authed_client) -> None:
    # Arrange
    part_id = _make_part(authed_client)

    # Act — a plain manual custom field, the way the UI writes one.
    created = authed_client.post(
        "/api/custom-fields",
        json={
            "object_type": "part",
            "object_id": part_id,
            "key": "Resistance",
            "value": "10 kΩ",
        },
    )

    # Assert — additive: the existing keys are untouched, the new two are
    # present and NULL.
    assert created.status_code == 201, created.text
    row = created.json()["data"]
    assert row["source"] == "manual"
    assert row["original_value"] is None
    assert row["provider"] is None
    assert row["value_num"] is None

    listed = authed_client.get(
        f"/api/custom-fields/by-object/part/{part_id}"
    ).json()["data"]
    assert listed[0]["provider"] is None
    assert listed[0]["value_num"] is None


def test_value_num_round_trips_exactly(authed_client, db) -> None:
    # Arrange — a picofarad, which a double cannot hold exactly.
    part_id = _make_part(authed_client)
    authed_client.post(
        "/api/custom-fields",
        json={
            "object_type": "part",
            "object_id": part_id,
            "key": "Capacitance",
            "value": "1 pF",
        },
    )
    stored = db.execute(
        select(CustomField).where(CustomField.object_id == uuid.UUID(part_id))
    ).scalar_one()

    # Act
    stored.provider = "digikey"
    stored.value_num = Decimal("0.000000000001")
    db.commit()

    # Assert — exact, not approximate.
    db.refresh(stored)
    assert stored.provider == "digikey"
    assert stored.value_num == Decimal("0.000000000001")

    served = authed_client.get(
        f"/api/custom-fields/by-object/part/{part_id}"
    ).json()["data"]
    assert served[0]["provider"] == "digikey"
    # Sent as a string so the exactness survives JSON.
    assert Decimal(served[0]["value_num"]) == Decimal("0.000000000001")


def test_the_partial_index_exists_and_is_scoped_to_parsed_rows(db) -> None:
    # Arrange / Act — the index is what makes a server-side spec sort
    # affordable; losing it in a later migration should fail loudly here.
    definition = db.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
        {"name": "ix_custom_fields_ws_key_value_num"},
    ).scalar_one()

    # Assert
    assert "workspace_id" in definition
    assert "value_num" in definition
    assert "value_num IS NOT NULL" in definition
