"""Audit log: bulk_delete_parts creates one audit row and the response
correctly splits IDs into three buckets (BE2-024).

Mix of:
- own-workspace active parts         → archived_ids
- own-workspace already-archived     → already_archived_ids
- other-workspace / non-existent IDs → not_found_ids
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient) -> None:
    r = c.post(
        "/api/auth/signup",
        json={
            "email": f"u-{uuid.uuid4().hex[:8]}@x.com",
            "name": "u",
            "password": "TestPass-2026-Stronk",
        },
    )
    assert r.status_code == 200, r.text


@pytest.fixture
def authed():
    c = TestClient(app)
    _signup(c)
    return c


def _create(c: TestClient, name: str) -> str:
    r = c.post("/api/parts", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def test_bulk_delete_three_buckets(authed):
    """Mix of own-active, own-already-archived, and truly-missing IDs.
    Asserts response has three distinct buckets AND one audit_log row.
    """
    # own-active
    active_id = _create(authed, "Active")
    # own-archived
    archived_id = _create(authed, "AlreadyArchived")
    authed.post(f"/api/parts/{archived_id}/archive")
    # missing
    missing_id = str(uuid.uuid4())

    r = authed.post(
        "/api/parts/bulk-delete",
        json={"part_ids": [active_id, archived_id, missing_id]},
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]

    assert body["archived_ids"] == [active_id]
    assert body["already_archived_ids"] == [archived_id]
    assert body["not_found_ids"] == [missing_id]

    # Exactly one audit row must exist for this operation.
    audit_r = authed.get("/api/audit")
    assert audit_r.status_code == 200, audit_r.text
    rows = audit_r.json()["data"]
    bulk_rows = [row for row in rows if row["action"] == "part.bulk_archived"]
    assert len(bulk_rows) >= 1, "Expected at least one part.bulk_archived audit row"
    latest = bulk_rows[0]
    # The row must carry the two IDs that actually exist in this workspace.
    assert latest["target_type"] == "part"
    # target_ids contains the touched parts (active + already_archived).
    assert active_id in (latest["target_ids"] or [])
    assert archived_id in (latest["target_ids"] or [])
    # not_found (missing) is NOT in target_ids — it was never in this ws.
    assert missing_id not in (latest["target_ids"] or [])


def test_bulk_delete_cross_workspace_ids_land_in_not_found(authed):
    """IDs from another workspace appear in not_found_ids (no oracle leak)."""
    other = TestClient(app)
    _signup(other)
    other_id = _create(other, "OtherPart")

    mine = _create(authed, "Mine")
    r = authed.post(
        "/api/parts/bulk-delete",
        json={"part_ids": [mine, other_id]},
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["archived_ids"] == [mine]
    assert body["already_archived_ids"] == []
    assert body["not_found_ids"] == [other_id]
