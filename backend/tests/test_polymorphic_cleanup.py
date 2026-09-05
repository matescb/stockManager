"""Tests for domain/_polymorphic_cleanup.py.

Covers:
- purge_polymorphic deletes attachment, custom_field, tag_link and
  object_code rows belonging to the given (workspace_id, object_type,
  object_id). The object_code half is exercised in detail by
  tests/test_object_codes.py, which owns that table.
- Cross-workspace safety: purge with workspace_id=B deletes 0 rows from
  workspace A's data.
- SQLAlchemy before_delete listeners purge rows when polymorphic parents
  are hard-deleted.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain._polymorphic_cleanup import purge_polymorphic
from app.domain.attachments.models import Attachment
from app.domain.builds.models import Build
from app.domain.custom_fields.models import CustomField
from app.domain.lots.models import Lot
from app.domain.orders.models import Order
from app.domain.parts.models import Part
from app.domain.projects.models import Project
from app.domain.storage.models import StorageLocation
from app.domain.tags.models import Tag, TagLink
from app.main import app

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


def _add_polymorphic_rows(
    db,
    *,
    workspace_id: uuid.UUID,
    object_type: str,
    object_id: uuid.UUID,
) -> None:
    tag = Tag(
        workspace_id=workspace_id,
        name=f"tag-{uuid.uuid4().hex[:8]}",
        color="#f00",
    )
    db.add(tag)
    db.flush()
    db.add_all(
        [
            Attachment(
                workspace_id=workspace_id,
                object_type=object_type,
                object_id=object_id,
                file_name="file.pdf",
                file_type="datasheet",
                mime_type="application/pdf",
                size_bytes=12,
                storage_key=f"{workspace_id}/{uuid.uuid4()}-file.pdf",
            ),
            CustomField(
                workspace_id=workspace_id,
                object_type=object_type,
                object_id=object_id,
                key=f"field-{uuid.uuid4().hex[:8]}",
                value="v",
            ),
            TagLink(
                workspace_id=workspace_id,
                tag_id=tag.id,
                object_type=object_type,
                object_id=object_id,
            ),
        ]
    )
    db.flush()


def _polymorphic_counts(
    db,
    *,
    workspace_id: uuid.UUID,
    object_type: str,
    object_id: uuid.UUID,
) -> dict[str, int]:
    counts = {}
    for name, Model in (
        ("attachments", Attachment),
        ("custom_fields", CustomField),
        ("tag_links", TagLink),
    ):
        counts[name] = len(
            db.execute(
                select(Model).where(
                    Model.workspace_id == workspace_id,
                    Model.object_type == object_type,
                    Model.object_id == object_id,
                )
            ).scalars().all()
        )
    return counts


def _make_parent(db, *, workspace_id: uuid.UUID, object_type: str):
    if object_type == "part":
        parent = Part(workspace_id=workspace_id, name="Parent Part", part_type="local")
    elif object_type == "project":
        parent = Project(workspace_id=workspace_id, name="Parent Project")
    elif object_type == "order":
        parent = Order(workspace_id=workspace_id, name="Parent Order")
    elif object_type == "storage_location":
        parent = StorageLocation(workspace_id=workspace_id, name="Parent Bin")
    elif object_type == "build":
        project = Project(workspace_id=workspace_id, name="Build Project")
        db.add(project)
        db.flush()
        parent = Build(workspace_id=workspace_id, project_id=project.id, name="Parent Build")
    elif object_type == "lot":
        part = Part(workspace_id=workspace_id, name="Lot Part", part_type="local")
        db.add(part)
        db.flush()
        parent = Lot(workspace_id=workspace_id, part_id=part.id, source_type="manual")
    else:
        raise AssertionError(f"unknown test object_type {object_type}")
    db.add(parent)
    db.flush()
    return parent


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

    # Return dict has one key per registered child table. `object_codes`
    # joined the set in alembic 0073 — see tests/test_object_codes.py for
    # the code-specific hard-delete coverage.
    assert set(counts.keys()) == {
        "attachments",
        "custom_fields",
        "tag_links",
        "object_codes",
    }
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


@pytest.mark.parametrize(
    "object_type",
    ["part", "order", "project", "build", "lot", "storage_location"],
)
def test_hard_delete_parent_purges_polymorphic_rows(db, object_type):
    c = TestClient(app)
    ws_id = _signup(c)
    ws_uuid = uuid.UUID(ws_id)
    parent = _make_parent(db, workspace_id=ws_uuid, object_type=object_type)
    _add_polymorphic_rows(
        db,
        workspace_id=ws_uuid,
        object_type=object_type,
        object_id=parent.id,
    )

    assert _polymorphic_counts(
        db,
        workspace_id=ws_uuid,
        object_type=object_type,
        object_id=parent.id,
    ) == {"attachments": 1, "custom_fields": 1, "tag_links": 1}

    db.delete(parent)
    db.flush()

    assert _polymorphic_counts(
        db,
        workspace_id=ws_uuid,
        object_type=object_type,
        object_id=parent.id,
    ) == {"attachments": 0, "custom_fields": 0, "tag_links": 0}


def test_polymorphic_cleanup_listener_registration_is_idempotent(db, monkeypatch):
    from app.domain import _polymorphic_cleanup as cleanup

    cleanup.register_polymorphic_cleanup_listeners()
    cleanup.register_polymorphic_cleanup_listeners()

    calls = 0
    real_purge = cleanup._purge_polymorphic

    def counted_purge(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_purge(*args, **kwargs)

    monkeypatch.setattr(cleanup, "_purge_polymorphic", counted_purge)

    c = TestClient(app)
    ws_id = _signup(c)
    ws_uuid = uuid.UUID(ws_id)
    parent = _make_parent(db, workspace_id=ws_uuid, object_type="part")
    _add_polymorphic_rows(
        db,
        workspace_id=ws_uuid,
        object_type="part",
        object_id=parent.id,
    )

    db.delete(parent)
    db.flush()

    assert calls == 1
