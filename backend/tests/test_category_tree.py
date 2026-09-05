"""`part_categories.parent_id` — the tree half of `/api/categories`.

Covers the three service-layer guards (`domain/categories/tree.py`), the
archive-promotes-children rule, workspace isolation of `parent_id` in both
directions, and the 0078 DB trigger as a backstop.

`test_categories.py` still owns the flat CRUD, uniqueness and audit walk.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.audit.models import AuditLog
from app.domain.categories.tree import MAX_DEPTH
from app.main import app
from tests._factories import signup_user


@pytest.fixture
def other_client(db):
    """A second workspace, for the cross-workspace probes."""
    c = TestClient(app)
    signup_user(c)
    return c


def _create(client: TestClient, name: str, **body) -> dict:
    r = client.post("/api/categories", json={"name": name, **body})
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _chain(client: TestClient, depth: int, prefix: str = "Level") -> list[dict]:
    """Create `depth` categories nested one inside the next, root first.

    `prefix` keeps two chains in the same workspace from colliding on
    `uq_part_categories_ws_name`, which is workspace-global rather than
    sibling-scoped (see the comment on `PartCategory.library_slug`).
    """
    made: list[dict] = []
    parent_id = None
    for level in range(depth):
        made.append(_create(client, f"{prefix} {level}", parent_id=parent_id))
        parent_id = made[-1]["id"]
    return made


def _list(client: TestClient) -> list[dict]:
    r = client.get("/api/categories")
    assert r.status_code == 200, r.text
    return r.json()["data"]


# ---------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------


def test_create_child_and_read_parent_back(authed_client):
    parent = _create(authed_client, "Passives")
    child = _create(authed_client, "Resistors", parent_id=parent["id"])

    assert child["parent_id"] == parent["id"]
    assert parent["parent_id"] is None

    by_id = {row["id"]: row for row in _list(authed_client)}
    assert by_id[child["id"]]["parent_id"] == parent["id"]
    assert by_id[parent["id"]]["parent_id"] is None


def test_patch_reparents_and_null_returns_to_root(authed_client):
    a = _create(authed_client, "Passives")
    b = _create(authed_client, "Actives")
    child = _create(authed_client, "Resistors", parent_id=a["id"])

    moved = authed_client.patch(
        f"/api/categories/{child['id']}", json={"parent_id": b["id"]}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["data"]["parent_id"] == b["id"]

    # An explicit null is a real request ("promote to root"), not "leave
    # alone" — parent_id is nullable, unlike the fields in
    # `_NON_NULLABLE_PATCH_FIELDS`.
    rooted = authed_client.patch(
        f"/api/categories/{child['id']}", json={"parent_id": None}
    )
    assert rooted.status_code == 200, rooted.text
    assert rooted.json()["data"]["parent_id"] is None


def test_reparent_is_audited(authed_client, db):
    parent = _create(authed_client, "Passives")
    child = _create(authed_client, "Resistors")
    authed_client.patch(
        f"/api/categories/{child['id']}", json={"parent_id": parent["id"]}
    )

    rows = list(
        db.execute(
            select(AuditLog)
            .where(AuditLog.action == "category.updated")
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        ).scalars()
    )
    assert rows, "reparenting must write an audit row"
    assert "parent_id" in (rows[0].comment or "")
    assert uuid.UUID(child["id"]) in (rows[0].target_ids or [])


# ---------------------------------------------------------------------
# Cycle guard
# ---------------------------------------------------------------------


def test_self_parent_is_rejected(authed_client):
    category = _create(authed_client, "Passives")
    r = authed_client.patch(
        f"/api/categories/{category['id']}", json={"parent_id": category["id"]}
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "category.parent_cycle"


def test_reparent_under_own_descendant_is_rejected(authed_client):
    root, mid, leaf = _chain(authed_client, 3)

    # root -> leaf would detach the whole component from every root.
    r = authed_client.patch(
        f"/api/categories/{root['id']}", json={"parent_id": leaf["id"]}
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "category.parent_cycle"

    # ...and the stored tree is untouched.
    by_id = {row["id"]: row for row in _list(authed_client)}
    assert by_id[root["id"]]["parent_id"] is None
    assert by_id[mid["id"]]["parent_id"] == root["id"]
    assert by_id[leaf["id"]]["parent_id"] == mid["id"]


def test_reparent_under_direct_child_is_rejected(authed_client):
    parent = _create(authed_client, "Passives")
    child = _create(authed_client, "Resistors", parent_id=parent["id"])
    r = authed_client.patch(
        f"/api/categories/{parent['id']}", json={"parent_id": child["id"]}
    )
    assert r.status_code == 422
    assert r.json()["code"] == "category.parent_cycle"


def test_moving_a_node_sideways_within_its_own_subtree_is_allowed(authed_client):
    """A descendant may be reparented onto a *different* branch. Only the
    ancestors of the proposed parent matter, so this must not be caught by
    the cycle guard."""
    root, mid, leaf = _chain(authed_client, 3)
    sibling = _create(authed_client, "Capacitors", parent_id=root["id"])

    r = authed_client.patch(
        f"/api/categories/{leaf['id']}", json={"parent_id": sibling["id"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["parent_id"] == sibling["id"]
    assert mid["id"] != sibling["id"]


# ---------------------------------------------------------------------
# Depth cap
# ---------------------------------------------------------------------


def test_depth_cap_allows_exactly_max_depth_and_refuses_one_more(authed_client):
    chain = _chain(authed_client, MAX_DEPTH)
    assert len(chain) == MAX_DEPTH

    r = authed_client.post(
        "/api/categories",
        json={"name": "One too deep", "parent_id": chain[-1]["id"]},
    )
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "category.too_deep"
    assert body["max_depth"] == MAX_DEPTH


def test_depth_cap_accounts_for_the_moved_subtree_not_just_the_moved_node(
    authed_client,
):
    """Dragging a 3-level branch under a node at depth `MAX_DEPTH - 2` puts
    the branch's *leaf* past the cap even though the moved node itself
    would land inside it. The check must use the subtree's height."""
    trunk = _chain(authed_client, MAX_DEPTH - 2, prefix="Trunk")
    branch = _chain(authed_client, 3, prefix="Branch")  # root + two levels

    # The moved node alone would land at depth (MAX_DEPTH - 2) + 1, which
    # is fine; its leaf would land two deeper, which is not.
    assert (MAX_DEPTH - 2) + 1 <= MAX_DEPTH
    r = authed_client.patch(
        f"/api/categories/{branch[0]['id']}", json={"parent_id": trunk[-1]["id"]}
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "category.too_deep"

    # A leaf from the same branch fits, because it carries nothing.
    r = authed_client.patch(
        f"/api/categories/{branch[-1]['id']}", json={"parent_id": trunk[-1]['id']}
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------
# Archived parents
# ---------------------------------------------------------------------


def test_archived_category_cannot_be_used_as_a_parent(authed_client):
    parent = _create(authed_client, "Passives")
    assert authed_client.post(
        f"/api/categories/{parent['id']}/archive"
    ).status_code == 200

    r = authed_client.post(
        "/api/categories", json={"name": "Resistors", "parent_id": parent["id"]}
    )
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "category.archived"


def test_archive_promotes_children_to_root(authed_client, db):
    """`ON DELETE SET NULL` promotes orphans on a hard delete; archive is
    the only delete the UI offers, so it does the same thing — otherwise
    the active tree would contain children whose parent is not in it."""
    root = _create(authed_client, "Passives")
    child_a = _create(authed_client, "Resistors", parent_id=root["id"])
    child_b = _create(authed_client, "Capacitors", parent_id=root["id"])
    grandchild = _create(authed_client, "Thin film", parent_id=child_a["id"])

    assert authed_client.post(
        f"/api/categories/{root['id']}/archive"
    ).status_code == 200

    by_id = {row["id"]: row for row in _list(authed_client)}
    assert by_id[child_a["id"]]["parent_id"] is None
    assert by_id[child_b["id"]]["parent_id"] is None
    # Only DIRECT children move. The grandchild stays where it was — its
    # own parent is still active and still in the tree.
    assert by_id[grandchild["id"]]["parent_id"] == child_a["id"]

    rows = list(
        db.execute(
            select(AuditLog)
            .where(AuditLog.action == "category.archived")
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        ).scalars()
    )
    assert rows[0].comment == "promoted_children=2"


def test_archiving_a_leaf_records_no_promotion(authed_client, db):
    leaf = _create(authed_client, "Resistors")
    assert authed_client.post(
        f"/api/categories/{leaf['id']}/archive"
    ).status_code == 200

    rows = list(
        db.execute(
            select(AuditLog)
            .where(AuditLog.action == "category.archived")
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        ).scalars()
    )
    assert rows[0].comment is None


def test_archived_children_are_promoted_too(authed_client):
    """An archived child that is later restored must not come back
    pointing at a parent that is itself archived — the restore would
    resurrect it into a component no tree render can reach."""
    root = _create(authed_client, "Passives")
    child = _create(authed_client, "Resistors", parent_id=root["id"])

    assert authed_client.post(
        f"/api/categories/{child['id']}/archive"
    ).status_code == 200
    assert authed_client.post(
        f"/api/categories/{root['id']}/archive"
    ).status_code == 200
    assert authed_client.post(
        f"/api/categories/{child['id']}/restore"
    ).status_code == 200

    by_id = {row["id"]: row for row in _list(authed_client)}
    assert by_id[child["id"]]["parent_id"] is None


# ---------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------


def test_create_with_foreign_parent_is_404(authed_client, other_client):
    foreign = _create(other_client, "Their category")
    r = authed_client.post(
        "/api/categories", json={"name": "Mine", "parent_id": foreign["id"]}
    )
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "category.not_found"


def test_patch_to_foreign_parent_is_404(authed_client, other_client):
    foreign = _create(other_client, "Their category")
    mine = _create(authed_client, "Mine")
    r = authed_client.patch(
        f"/api/categories/{mine['id']}", json={"parent_id": foreign["id"]}
    )
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "category.not_found"

    # Not persisted, and not leaked into the other workspace's listing.
    assert _list(authed_client)[0]["parent_id"] is None
    assert [row["parent_id"] for row in _list(other_client)] == [None]


def test_missing_parent_and_foreign_parent_are_indistinguishable(
    authed_client, other_client
):
    """404 for both, same code, same message — a foreign UUID must not be
    an existence oracle (ADR-0002)."""
    foreign = _create(other_client, "Their category")
    missing = str(uuid.uuid4())

    a = authed_client.post(
        "/api/categories", json={"name": "A", "parent_id": foreign["id"]}
    )
    b = authed_client.post(
        "/api/categories", json={"name": "B", "parent_id": missing}
    )
    assert a.status_code == b.status_code == 404
    # `request_id` is per-request by construction; everything a caller
    # could distinguish the two cases by must match.
    assert {k: v for k, v in a.json().items() if k != "request_id"} == {
        k: v for k, v in b.json().items() if k != "request_id"
    }


def test_archive_promotion_never_crosses_a_workspace(authed_client, other_client):
    """Behavioural half: another workspace's tree is untouched."""
    mine_root = _create(authed_client, "Passives")
    _create(authed_client, "Resistors", parent_id=mine_root["id"])
    their_root = _create(other_client, "Passives")
    their_child = _create(other_client, "Resistors", parent_id=their_root["id"])

    assert authed_client.post(
        f"/api/categories/{mine_root['id']}/archive"
    ).status_code == 200

    theirs = {row["id"]: row for row in _list(other_client)}
    assert theirs[their_child["id"]]["parent_id"] == their_root["id"]


def test_archive_promotion_update_names_the_workspace(authed_client, db):
    """...and the half that can actually fail.

    The test above cannot: UUIDs never collide across workspaces, and the
    0078 trigger blocks seeding a real collision, so it would still pass
    with `.where(PartCategory.workspace_id == ws.id)` deleted from
    `archive_category`'s promoting UPDATE. That one `.where` is exactly
    what the workspace-isolation invariant turns on for a bulk write with
    no ORM row to hang a check off, so assert the emitted SQL directly.
    """
    from sqlalchemy import event

    # Listen on the *session's* bind, not `app.infra.db._engine`:
    # `conftest.py` builds its own engine and binds every test session to
    # one connection off it, so a listener on the production engine would
    # never fire and this test would pass vacuously — the exact failure
    # mode it exists to prevent.
    bind = db.get_bind()
    statements: list[str] = []

    def _capture(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE PART_CATEGORIES"):
            statements.append(statement)

    event.listen(bind, "before_cursor_execute", _capture)
    try:
        root = _create(authed_client, "Passives")
        _create(authed_client, "Resistors", parent_id=root["id"])
        assert authed_client.post(
            f"/api/categories/{root['id']}/archive"
        ).status_code == 200
    finally:
        event.remove(bind, "before_cursor_execute", _capture)

    promoting = [s for s in statements if "parent_id" in s.lower()]
    assert promoting, "archive emitted no promoting UPDATE"
    assert "workspace_id" in promoting[0].lower(), promoting[0]


@pytest.mark.real_db
def test_parent_workspace_trigger_blocks_raw_cross_workspace_sql():
    """Migration 0078's BEFORE trigger. The service layer 404s a foreign
    parent long before SQL; this proves raw SQL can't smuggle one in
    either. SQLSTATE must be WS001 so `raise_integrity_as_409` maps it.

    real_db: the UPDATE runs in its own session, so the API-created rows
    have to be genuinely committed to be visible to it.
    """
    from sqlalchemy import text as sa_text
    from sqlalchemy.exc import DBAPIError, IntegrityError

    from app.infra.db import SessionLocal

    a = TestClient(app)
    b = TestClient(app)
    signup_user(a)
    signup_user(b)
    foreign = _create(b, "Their category")
    mine = _create(a, "My category")

    with SessionLocal() as s:
        with pytest.raises((IntegrityError, DBAPIError)) as excinfo:
            s.execute(
                sa_text(
                    "UPDATE part_categories SET parent_id = :pid WHERE id = :cid"
                ),
                {"pid": foreign["id"], "cid": mine["id"]},
            )
            s.commit()
    assert getattr(excinfo.value.orig, "sqlstate", None) == "WS001"
