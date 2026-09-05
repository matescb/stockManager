"""Tests for `POST /api/parts/{part_id}/replace-in-projects`.

Covers the service directly (unit), the route (integration), and the
workspace-isolation contract (a cross-workspace part/project id is rejected
and no foreign row is touched).
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.audit.models import AuditLog
from app.domain.parts.models import Part
from app.domain.projects.models import ProjectEntry
from app.domain.projects.replace_part import replace_part_in_projects
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace
from app.main import app
from tests._factories import (
    create_part,
    create_project_with_bom,
    signup_user,
)


def _entry_part_ids(db, project_id: str) -> list[str | None]:
    rows = db.execute(
        select(ProjectEntry).where(ProjectEntry.project_id == uuid.UUID(project_id))
    ).scalars()
    return [str(e.part_id) if e.part_id else None for e in rows]


# --------------------------------------------------------------------------
# Service (unit)
# --------------------------------------------------------------------------


def test_service_replaces_across_all_active_projects(db, authed_client):
    src = create_part(authed_client, "Old Cap")
    dst = create_part(authed_client, "New Cap")
    other = create_part(authed_client, "Unrelated")
    p1 = create_project_with_bom(
        authed_client,
        "P1",
        [{"part_id": src, "quantity": 2}, {"part_id": other, "quantity": 1}],
    )
    p2 = create_project_with_bom(authed_client, "P2", [{"part_id": src, "quantity": 5}])
    p3 = create_project_with_bom(authed_client, "P3", [{"part_id": other, "quantity": 1}])

    ws = db.execute(select(Workspace)).scalars().one()
    user = db.execute(select(User)).scalars().one()
    source_part = db.get(Part, uuid.UUID(src))
    target_part = db.get(Part, uuid.UUID(dst))

    result = replace_part_in_projects(
        db,
        workspace=ws,
        user=user,
        source_part=source_part,
        target_part=target_part,
        project_ids=None,
    )

    # One matching line in P1 and one in P2; P3 has none.
    assert result.updated_entries == 2
    assert result.affected_projects == 2
    assert set(_entry_part_ids(db, p1)) == {dst, other}
    assert _entry_part_ids(db, p2) == [dst]
    assert _entry_part_ids(db, p3) == [other]


def test_service_scopes_to_selected_project_only(db, authed_client):
    src = create_part(authed_client, "Old")
    dst = create_part(authed_client, "New")
    p1 = create_project_with_bom(authed_client, "P1", [{"part_id": src, "quantity": 1}])
    p2 = create_project_with_bom(authed_client, "P2", [{"part_id": src, "quantity": 1}])

    ws = db.execute(select(Workspace)).scalars().one()
    user = db.execute(select(User)).scalars().one()

    result = replace_part_in_projects(
        db,
        workspace=ws,
        user=user,
        source_part=db.get(Part, uuid.UUID(src)),
        target_part=db.get(Part, uuid.UUID(dst)),
        project_ids=[uuid.UUID(p1)],
    )

    assert result.updated_entries == 1
    assert result.affected_projects == 1
    assert _entry_part_ids(db, p1) == [dst]
    assert _entry_part_ids(db, p2) == [src]  # untouched


# --------------------------------------------------------------------------
# Route (integration)
# --------------------------------------------------------------------------


def test_route_replaces_and_returns_counts(authed_client):
    src = create_part(authed_client, "Old")
    dst = create_part(authed_client, "New")
    p1 = create_project_with_bom(authed_client, "P1", [{"part_id": src, "quantity": 2}])
    p2 = create_project_with_bom(authed_client, "P2", [{"part_id": src, "quantity": 1}])

    r = authed_client.post(
        f"/api/parts/{src}/replace-in-projects", json={"target_part_id": dst}
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"updated_entries": 2, "affected_projects": 2}

    for pid in (p1, p2):
        entries = authed_client.get(f"/api/projects/{pid}/entries").json()["data"]
        assert entries and all(e["part_id"] == dst for e in entries)


def test_route_scopes_to_selected_projects(authed_client):
    src = create_part(authed_client, "Old")
    dst = create_part(authed_client, "New")
    p1 = create_project_with_bom(authed_client, "P1", [{"part_id": src, "quantity": 1}])
    p2 = create_project_with_bom(authed_client, "P2", [{"part_id": src, "quantity": 1}])

    r = authed_client.post(
        f"/api/parts/{src}/replace-in-projects",
        json={"target_part_id": dst, "project_ids": [p1]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"updated_entries": 1, "affected_projects": 1}

    e2 = authed_client.get(f"/api/projects/{p2}/entries").json()["data"]
    assert all(e["part_id"] == src for e in e2)


def test_route_rejects_same_source_and_target(authed_client):
    src = create_part(authed_client, "Old")
    r = authed_client.post(
        f"/api/parts/{src}/replace-in-projects", json={"target_part_id": src}
    )
    assert r.status_code == 400
    assert r.json()["code"] == "part.replace_same_target"


def test_route_rejects_archived_target(authed_client):
    src = create_part(authed_client, "Old")
    dst = create_part(authed_client, "New")
    create_project_with_bom(authed_client, "P1", [{"part_id": src, "quantity": 1}])
    # Archive the intended replacement — binding a retired part into a BOM
    # is the BE2-016 vector the route must refuse.
    assert authed_client.post(f"/api/parts/{dst}/archive").status_code == 200

    r = authed_client.post(
        f"/api/parts/{src}/replace-in-projects", json={"target_part_id": dst}
    )
    assert r.status_code == 404


def test_route_missing_target_body_is_422(authed_client):
    src = create_part(authed_client, "Old")
    r = authed_client.post(f"/api/parts/{src}/replace-in-projects", json={})
    assert r.status_code == 422


def test_route_writes_audit_rows(db, authed_client):
    src = create_part(authed_client, "Old")
    dst = create_part(authed_client, "New")
    create_project_with_bom(authed_client, "P1", [{"part_id": src, "quantity": 1}])
    create_project_with_bom(authed_client, "P2", [{"part_id": src, "quantity": 1}])

    r = authed_client.post(
        f"/api/parts/{src}/replace-in-projects", json={"target_part_id": dst}
    )
    assert r.status_code == 200, r.text

    actions = [a.action for a in db.execute(select(AuditLog)).scalars()]
    assert actions.count("project.part_replaced") == 2
    assert actions.count("part.replaced_in_projects") == 1


# --------------------------------------------------------------------------
# Workspace isolation
# --------------------------------------------------------------------------


def test_cross_workspace_part_and_project_ids_rejected():
    a = TestClient(app)
    b = TestClient(app)
    signup_user(a, email=f"a-{uuid.uuid4().hex[:6]}@x.com")
    signup_user(b, email=f"b-{uuid.uuid4().hex[:6]}@x.com")

    a_src = create_part(a, "A source")
    a_dst = create_part(a, "A dest")
    a_proj = create_project_with_bom(a, "A proj", [{"part_id": a_src, "quantity": 1}])

    b_src = create_part(b, "B source")
    b_dst = create_part(b, "B dest")

    # 1) B cannot drive the operation from A's part id (path) -> 404.
    r = b.post(
        f"/api/parts/{a_src}/replace-in-projects", json={"target_part_id": b_dst}
    )
    assert r.status_code == 404

    # 2) B cannot name A's part as the replacement target -> 404.
    r = b.post(
        f"/api/parts/{b_src}/replace-in-projects", json={"target_part_id": a_dst}
    )
    assert r.status_code == 404

    # 3) B naming A's project in project_ids -> 404 (whole op rolls back).
    r = b.post(
        f"/api/parts/{b_src}/replace-in-projects",
        json={"target_part_id": b_dst, "project_ids": [a_proj]},
    )
    assert r.status_code == 404

    # A's BOM is untouched throughout.
    entries = a.get(f"/api/projects/{a_proj}/entries").json()["data"]
    assert all(e["part_id"] == a_src for e in entries)
