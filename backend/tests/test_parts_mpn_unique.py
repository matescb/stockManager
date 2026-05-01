"""MPN uniqueness per workspace + name-defaults-to-MPN.

The user's invariant: "Each MPN can have only one part" (within a
workspace). The partial unique index in alembic 0011 enforces this at
the DB level; the create_part route runs an explicit pre-check so the
409 response can name the existing part.

Bag-import already had MPN-collision detection (it returns
status='duplicate' rather than 409); these tests don't change that
path — they cover create_part directly.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient) -> None:
    r = c.post(
        "/api/auth/signup",
        json={"email": f"u-{uuid.uuid4().hex[:8]}@x.com", "name": "u", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text


@pytest.fixture
def authed():
    c = TestClient(app)
    _signup(c)
    return c


def test_create_part_name_defaults_to_mpn(authed):
    """When the operator pastes only an MPN, name should default to it
    server-side rather than 422'ing on the missing-name."""
    r = authed.post("/api/parts", json={"part_type": "linked", "mpn": "RC0402JR-070R"})
    assert r.status_code == 201, r.text
    body = r.json()["data"]
    assert body["name"] == "RC0402JR-070R"
    assert body["mpn"] == "RC0402JR-070R"


def test_create_part_requires_name_or_mpn(authed):
    r = authed.post("/api/parts", json={"part_type": "local"})
    # The route raises 422 explicitly; the exception handler maps that.
    assert r.status_code == 422, r.text
    assert "name" in r.json()["status"]["message"].lower()


def test_create_part_blank_name_with_mpn_uses_mpn(authed):
    """Blank-string name (vs missing key) should also fall back to MPN."""
    r = authed.post(
        "/api/parts",
        json={"part_type": "linked", "name": "   ", "mpn": "ABC-123"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["data"]["name"] == "ABC-123"


def test_create_part_409_on_mpn_collision(authed):
    first = authed.post("/api/parts", json={"name": "Resistor", "mpn": "RC0402JR-070R"})
    assert first.status_code == 201
    first_id = first.json()["data"]["id"]

    r = authed.post("/api/parts", json={"name": "Different name", "mpn": "RC0402JR-070R"})
    assert r.status_code == 409, r.text
    body = r.json()
    # The structured 409 response carries enough to deep-link to the existing part.
    assert body["existing_id"] == first_id
    assert body["existing_name"] == "Resistor"
    msg = body["status"]["message"].lower()
    assert "already used" in msg


def test_create_part_no_mpn_no_collision(authed):
    """Two MPN-less parts can coexist freely — the partial unique index
    excludes NULL mpn rows, so this is a NO-OP correctness check."""
    a = authed.post("/api/parts", json={"name": "Sub-assembly A"})
    b = authed.post("/api/parts", json={"name": "Sub-assembly B"})
    assert a.status_code == 201
    assert b.status_code == 201


def test_archived_part_does_not_block_new_mpn(authed):
    """The partial unique index excludes archived rows, so archiving a
    part frees up its MPN for a replacement."""
    first = authed.post("/api/parts", json={"name": "Old", "mpn": "RC0402JR-070R"})
    first_id = first.json()["data"]["id"]
    authed.post(f"/api/parts/{first_id}/archive")

    r = authed.post("/api/parts", json={"name": "Replacement", "mpn": "RC0402JR-070R"})
    assert r.status_code == 201, r.text
    assert r.json()["data"]["mpn"] == "RC0402JR-070R"


def test_two_parts_same_mpn_different_workspaces_ok(authed):
    """Workspace-scoped uniqueness — two operators in two workspaces can
    each track the same MPN. Workspace isolation is the existing test
    fixture's default; this is a sanity check that the constraint
    didn't accidentally global-scope itself."""
    other = TestClient(app)
    _signup(other)
    a = authed.post("/api/parts", json={"name": "Mine", "mpn": "RC0402JR-070R"})
    b = other.post("/api/parts", json={"name": "Theirs", "mpn": "RC0402JR-070R"})
    assert a.status_code == 201
    assert b.status_code == 201
