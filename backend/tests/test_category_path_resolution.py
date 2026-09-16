"""`categories/service.py` — resolving a name PATH onto a workspace's tree.

A4 hands provider import a path ("Capacitors / Ceramic"), not an id. This
is the lookup that turns one into a row, and the three properties that
matter are: it never creates anything, it never leaves the caller's
workspace, and it falls back to the root of the path rather than
inventing a leaf.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.domain.categories.models import PartCategory
from app.domain.categories.service import (
    category_name_path,
    resolve_category_path,
    resolve_category_path_or_root,
)
from app.main import app
from tests._factories import signup_user


def _ws(c: TestClient, email: str | None = None) -> uuid.UUID:
    return uuid.UUID(signup_user(c, email=email).json()["data"]["workspace_id"])


def _category(c: TestClient, name: str, parent_id: str | None = None) -> str:
    body: dict = {"name": name}
    if parent_id:
        body["parent_id"] = parent_id
    r = c.post("/api/categories", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


@pytest.fixture
def authed(db) -> TestClient:
    c = TestClient(app)
    _ws(c)
    return c


def _ws_id(c: TestClient) -> uuid.UUID:
    return uuid.UUID(c.get("/api/workspaces/current").json()["data"]["id"])


# ---------------------------------------------------------------------------
# resolve_category_path
# ---------------------------------------------------------------------------


def test_a_two_level_path_resolves_to_the_leaf(authed, db):
    root = _category(authed, "Capacitors")
    leaf = _category(authed, "Ceramic", parent_id=root)

    found = resolve_category_path(db, ws_id=_ws_id(authed), path="Capacitors / Ceramic")
    assert found is not None
    assert str(found.id) == leaf


def test_a_single_segment_path_resolves_to_a_root(authed, db):
    root = _category(authed, "Resistors")
    found = resolve_category_path(db, ws_id=_ws_id(authed), path="Resistors")
    assert found is not None and str(found.id) == root


def test_matching_is_case_and_space_insensitive(authed, db):
    root = _category(authed, "Capacitors")
    leaf = _category(authed, "Ceramic", parent_id=root)

    found = resolve_category_path(db, ws_id=_ws_id(authed), path="capacitors/CERAMIC")
    assert found is not None and str(found.id) == leaf


def test_a_missing_leaf_resolves_to_nothing(authed, db):
    _category(authed, "Capacitors")
    assert resolve_category_path(db, ws_id=_ws_id(authed), path="Capacitors / Ceramic") is None


def test_the_leaf_must_sit_under_the_named_parent(authed, db):
    """A category called "Ceramic" filed at the root is not
    "Capacitors / Ceramic" — the path is a claim about the shape."""
    _category(authed, "Capacitors")
    _category(authed, "Ceramic")
    assert resolve_category_path(db, ws_id=_ws_id(authed), path="Capacitors / Ceramic") is None


def test_an_archived_category_does_not_resolve(authed, db):
    root = _category(authed, "Capacitors")
    leaf = _category(authed, "Ceramic", parent_id=root)
    assert authed.post(f"/api/categories/{leaf}/archive").status_code == 200

    assert resolve_category_path(db, ws_id=_ws_id(authed), path="Capacitors / Ceramic") is None


def test_resolution_creates_nothing(authed, db):
    """The import path must never mint categories: a workspace's tree is
    curated, and a typo in a vendor taxonomy would otherwise grow it."""
    before = db.query(PartCategory).count()
    assert resolve_category_path(db, ws_id=_ws_id(authed), path="Diodes / Zener") is None
    assert db.query(PartCategory).count() == before


@pytest.mark.parametrize("path", [None, "", "   ", "/", " / "])
def test_an_empty_path_resolves_to_nothing(authed, db, path):
    assert resolve_category_path(db, ws_id=_ws_id(authed), path=path) is None


# ---------------------------------------------------------------------------
# resolve_category_path_or_root
# ---------------------------------------------------------------------------


def test_the_root_fallback_files_the_part_one_level_up(authed, db):
    """Until the sub-category seed (A6) runs, "Capacitors / Ceramic"
    exists on no workspace. Filing the part under Capacitors beats
    leaving 118 parts uncategorized."""
    root = _category(authed, "Capacitors")
    found = resolve_category_path_or_root(
        db, ws_id=_ws_id(authed), path="Capacitors / Ceramic"
    )
    assert found is not None and str(found.id) == root


def test_the_root_fallback_prefers_the_full_path(authed, db):
    root = _category(authed, "Capacitors")
    leaf = _category(authed, "Ceramic", parent_id=root)
    found = resolve_category_path_or_root(
        db, ws_id=_ws_id(authed), path="Capacitors / Ceramic"
    )
    assert found is not None and str(found.id) == leaf


def test_the_root_fallback_gives_up_when_the_root_is_absent(authed, db):
    assert (
        resolve_category_path_or_root(db, ws_id=_ws_id(authed), path="Capacitors / Ceramic")
        is None
    )


# ---------------------------------------------------------------------------
# category_name_path — the reverse direction, used to pick a spec schema
# ---------------------------------------------------------------------------


def test_the_name_path_of_a_leaf_joins_its_ancestors(authed, db):
    root = _category(authed, "Capacitors")
    leaf = _category(authed, "Ceramic", parent_id=root)
    path = category_name_path(db, ws_id=_ws_id(authed), category_id=uuid.UUID(leaf))
    assert path == "Capacitors / Ceramic"


def test_the_name_path_of_a_root_is_its_own_name(authed, db):
    root = _category(authed, "Resistors")
    path = category_name_path(db, ws_id=_ws_id(authed), category_id=uuid.UUID(root))
    assert path == "Resistors"


def test_the_name_path_of_an_unknown_category_is_none(authed, db):
    assert (
        category_name_path(db, ws_id=_ws_id(authed), category_id=uuid.uuid4()) is None
    )


# ---------------------------------------------------------------------------
# Workspace isolation (CLAUDE.md: enforced in code, not the DB)
# ---------------------------------------------------------------------------


def test_a_path_never_resolves_into_another_workspace(db):
    owner = TestClient(app)
    _ws(owner, "cat-path-owner@example.com")
    root = _category(owner, "Capacitors")
    _category(owner, "Ceramic", parent_id=root)

    other = TestClient(app)
    other_ws = _ws(other, "cat-path-other@example.com")

    assert resolve_category_path(db, ws_id=other_ws, path="Capacitors / Ceramic") is None
    assert (
        resolve_category_path_or_root(db, ws_id=other_ws, path="Capacitors / Ceramic")
        is None
    )


def test_a_name_path_never_reads_another_workspace(db):
    owner = TestClient(app)
    _ws(owner, "cat-name-owner@example.com")
    leaf = _category(owner, "Resistors")

    other = TestClient(app)
    other_ws = _ws(other, "cat-name-other@example.com")

    assert (
        category_name_path(db, ws_id=other_ws, category_id=uuid.UUID(leaf)) is None
    )
