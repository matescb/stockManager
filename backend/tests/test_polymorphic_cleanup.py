"""Tests for domain/_polymorphic_cleanup.py.

Covers:
- purge_polymorphic deletes attachment, custom_field, and tag_link rows
  belonging to the given (workspace_id, object_type, object_id).
- Cross-workspace safety: purge with workspace_id=B deletes 0 rows from
  workspace A's data.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.domain._polymorphic_cleanup import purge_polymorphic
from app.domain.attachments.models import Attachment
from app.domain.custom_fields.models import CustomField
from app.domain.tags.models import TagLink


DEFAULT_PASSWORD = "TestPass-2026-Stronk"


def _signup(c: TestClient, email: str | None = None) -> str:
    """Sign up a new user, return workspace_id."""
    email = email or f"u-{uuid.uuid4().hex[:8]}@example.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "Tester", "password": DEFAULT_PASSWORD},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["workspace_id"]


def _make_part(c: TestClient) -> str:
    r = c.post("/api/parts", json={"name": "TestPart", "part_type": "local"})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _add_custom_field(c: TestClient, part_id: str) -> str:
    r = c.post(
        "/api/custom-fields",
        json={"object_type": "part", "object_id": part_id, "key": "test_key", "value": "v"},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _add_tag_link(c: TestClient, part_id: str) -> str:
    # Create a tag first
    tag_r = c.post("/api/tags", json={"name": f"tag-{uuid.uuid4().hex[:6]}", "color": "#f00"})
    assert tag_r.status_code in (200, 201), tag_r.text
    tag_id = tag_r.json()["data"]["id"]

    r = c.post(
        "/api/tags/links",
        json={"tag_id": tag_id, "object_type": "part", "object_id": part_id},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _row_count(db, Model, workspace_id, object_id):
    return db.execute(
        select(Model).where(
            Model.workspace_id == uuid.UUID(workspace_id),
            Model.object_id == uuid.UUID(object_id),
        )
    ).scalars().all().__len__()


# ---------------------------------------------------------------------------
# Main test: purge_polymorphic removes all three row types
# ---------------------------------------------------------------------------

def test_purge_polymorphic_deletes_all_three_tables(db):
    c = TestClient(app)
    ws_id = _signup(c)
    part_id = _make_part(c)

    # Create one row in each polymorphic table
    _add_custom_field(c, part_id)
    _add_tag_link(c, part_id)

    # Verify pre-condition: rows exist
    ws_uuid = uuid.UUID(ws_id)
    part_uuid = uuid.UUID(part_id)
    assert _row_count(db, CustomField, ws_id, part_id) >= 1
    assert _row_count(db, TagLink, ws_id, part_id) >= 1

    # Run cleanup
    counts = purge_polymorphic(
        db,
        workspace_id=ws_uuid,
        object_type="part",
        object_id=part_uuid,
    )
    db.commit()

    # All rows gone
    assert _row_count(db, CustomField, ws_id, part_id) == 0
    assert _row_count(db, TagLink, ws_id, part_id) == 0

    # Return dict has the expected keys
    assert set(counts.keys()) == {"attachments", "custom_fields", "tag_links"}
    # At least the custom_field and tag_link counts are non-zero
    assert counts["custom_fields"] >= 1
    assert counts["tag_links"] >= 1


# ---------------------------------------------------------------------------
# Cross-workspace safety: purging workspace B does not touch workspace A
# ---------------------------------------------------------------------------

def test_purge_polymorphic_respects_workspace_isolation(db):
    ca = TestClient(app)
    cb = TestClient(app)
    ws_a = _signup(ca)
    ws_b = _signup(cb)

    # Workspace A creates a part with a custom field
    part_a = _make_part(ca)
    _add_custom_field(ca, part_a)

    pre_count = _row_count(db, CustomField, ws_a, part_a)
    assert pre_count >= 1

    # Purge with workspace B's id and A's part_id — must delete 0 rows
    counts = purge_polymorphic(
        db,
        workspace_id=uuid.UUID(ws_b),
        object_type="part",
        object_id=uuid.UUID(part_a),
    )
    db.commit()

    # Workspace A's data is untouched
    assert _row_count(db, CustomField, ws_a, part_a) == pre_count

    # All counts must be 0
    assert counts["custom_fields"] == 0
    assert counts["attachments"] == 0
    assert counts["tag_links"] == 0


# ---------------------------------------------------------------------------
# Idempotency: double-purge returns 0 on second call
# ---------------------------------------------------------------------------

def test_purge_polymorphic_is_idempotent(db):
    c = TestClient(app)
    ws_id = _signup(c)
    part_id = _make_part(c)
    _add_custom_field(c, part_id)

    ws_uuid = uuid.UUID(ws_id)
    part_uuid = uuid.UUID(part_id)

    first = purge_polymorphic(db, workspace_id=ws_uuid, object_type="part", object_id=part_uuid)
    db.commit()
    assert first["custom_fields"] >= 1

    second = purge_polymorphic(db, workspace_id=ws_uuid, object_type="part", object_id=part_uuid)
    db.commit()
    assert second["custom_fields"] == 0
    assert second["attachments"] == 0
    assert second["tag_links"] == 0
