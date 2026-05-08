from __future__ import annotations

import base64
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.domain.parts.models import Part
from app.domain.projects import bom_import as bom
from app.domain.projects.models import Project, ProjectEntry
from app.domain.projects.schemas import BomImportCommitIn, BomImportPreviewIn, BomMappingField
from app.domain.stock.models import StockEntry
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace, WorkspaceMember


def _setup_ws(db, name: str = "W") -> tuple[Workspace, User]:
    user = User(email=f"u-{uuid.uuid4().hex[:6]}@x.com", name="t", password_hash="x")
    db.add(user)
    db.flush()
    ws = Workspace(name=name, kind="organization", owner_user_id=user.id)
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, status="active"))
    db.commit()
    return ws, user


def _project(db, ws: Workspace, user: User, name: str = "Widget") -> Project:
    project = Project(workspace_id=ws.id, name=name, created_by=user.id, updated_by=user.id)
    db.add(project)
    db.commit()
    return project


def _b64(csv_text: str) -> str:
    return base64.b64encode(csv_text.encode()).decode()


def _mapping(*targets: str) -> list[BomMappingField]:
    return [BomMappingField(column_index=i, target=target) for i, target in enumerate(targets)]


def _commit_payload(
    csv_text: str,
    *,
    mapping: list[BomMappingField],
    auto_create_missing_parts: bool = True,
) -> BomImportCommitIn:
    return BomImportCommitIn(
        text_b64=_b64(csv_text),
        separator=",",
        encoding="utf-8",
        has_header=True,
        mapping=mapping,
        designator_separator=",",
        auto_create_missing_parts=auto_create_missing_parts,
    )


def _parts(db, ws: Workspace) -> list[Part]:
    return db.query(Part).filter(Part.workspace_id == ws.id).order_by(Part.name).all()


def _entries(db, project: Project) -> list[ProjectEntry]:
    return (
        db.query(ProjectEntry)
        .filter(ProjectEntry.project_id == project.id)
        .order_by(ProjectEntry.order_index)
        .all()
    )


def test_default_behaviour_unchanged_no_auto_create(db):
    ws, user = _setup_ws(db)
    project = _project(db, ws, user)
    payload = _commit_payload(
        "qty,mpn\n2,NEW-MPN\n",
        mapping=_mapping("quantity", "mpn"),
        auto_create_missing_parts=False,
    )

    result = bom.commit(db, workspace_id=ws.id, user_id=user.id, project=project, payload=payload)
    db.commit()

    assert result.inserted == 1
    assert result.matched == 0
    assert result.unmatched == 1
    assert result.auto_created == 0
    assert result.skipped == 0
    assert _parts(db, ws) == []
    entry = _entries(db, project)[0]
    assert entry.entry_type == "unmatched"
    assert entry.part_id is None


def test_auto_create_with_mpn(db):
    ws, user = _setup_ws(db)
    project = _project(db, ws, user)
    payload = _commit_payload("qty,mpn\n4,NEW-MPN\n", mapping=_mapping("quantity", "mpn"))

    result = bom.commit(db, workspace_id=ws.id, user_id=user.id, project=project, payload=payload)
    db.commit()

    assert result.inserted == 1
    assert result.auto_created == 1
    part = _parts(db, ws)[0]
    assert part.name == "NEW-MPN"
    assert part.mpn == "NEW-MPN"
    assert part.linked_provider == "none"
    assert part.description is None
    assert part.default_storage_location_id is None
    assert part.created_by == user.id
    assert db.query(StockEntry).filter(StockEntry.part_id == part.id).count() == 0
    entry = _entries(db, project)[0]
    assert entry.entry_type == "part"
    assert entry.part_id == part.id


def test_auto_create_with_name_only(db):
    ws, user = _setup_ws(db)
    project = _project(db, ws, user)
    payload = _commit_payload("qty,part\n3,Local resistor\n", mapping=_mapping("quantity", "part"))

    result = bom.commit(db, workspace_id=ws.id, user_id=user.id, project=project, payload=payload)
    db.commit()

    assert result.auto_created == 1
    part = _parts(db, ws)[0]
    assert part.name == "Local resistor"
    assert part.mpn is None


def test_auto_create_falls_back_to_mpn_for_name(db):
    ws, user = _setup_ws(db)
    project = _project(db, ws, user)
    payload = _commit_payload(
        "qty,mpn,part\n1,MPN-ONLY,\n",
        mapping=_mapping("quantity", "mpn", "part"),
    )

    result = bom.commit(db, workspace_id=ws.id, user_id=user.id, project=project, payload=payload)
    db.commit()

    assert result.auto_created == 1
    part = _parts(db, ws)[0]
    assert part.name == "MPN-ONLY"
    assert part.mpn == "MPN-ONLY"


def test_skip_row_with_neither_name_nor_mpn(db):
    ws, user = _setup_ws(db)
    project = _project(db, ws, user)
    payload = _commit_payload("qty,mpn,part\n5,,\n", mapping=_mapping("quantity", "mpn", "part"))

    result = bom.commit(db, workspace_id=ws.id, user_id=user.id, project=project, payload=payload)
    db.commit()

    assert result.inserted == 0
    assert result.auto_created == 0
    assert result.skipped == 1
    assert _parts(db, ws) == []
    assert _entries(db, project) == []


def test_two_rows_same_new_mpn_merge_into_one_part(db):
    ws, user = _setup_ws(db)
    project = _project(db, ws, user)
    payload = _commit_payload(
        "qty,mpn\n1,MERGE-MPN\n2,MERGE-MPN\n",
        mapping=_mapping("quantity", "mpn"),
    )

    result = bom.commit(db, workspace_id=ws.id, user_id=user.id, project=project, payload=payload)
    db.commit()

    parts = _parts(db, ws)
    entries = _entries(db, project)
    assert result.auto_created == 1
    assert len(parts) == 1
    assert [entry.part_id for entry in entries] == [parts[0].id, parts[0].id]


def test_two_rows_same_name_no_mpn_merge_into_one_part(db):
    ws, user = _setup_ws(db)
    project = _project(db, ws, user)
    payload = _commit_payload(
        "qty,part\n1,Same local name\n2,Same local name\n",
        mapping=_mapping("quantity", "part"),
    )

    result = bom.commit(db, workspace_id=ws.id, user_id=user.id, project=project, payload=payload)
    db.commit()

    parts = _parts(db, ws)
    entries = _entries(db, project)
    assert result.auto_created == 1
    assert len(parts) == 1
    assert [entry.part_id for entry in entries] == [parts[0].id, parts[0].id]


def test_existing_part_with_same_mpn_is_reused_not_recreated(db):
    ws, user = _setup_ws(db)
    part = Part(
        workspace_id=ws.id,
        name="Existing",
        mpn="EXISTING-MPN",
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(part)
    project = _project(db, ws, user)
    payload = _commit_payload("qty,mpn\n2,EXISTING-MPN\n", mapping=_mapping("quantity", "mpn"))

    result = bom.commit(db, workspace_id=ws.id, user_id=user.id, project=project, payload=payload)
    db.commit()

    assert result.matched == 1
    assert result.auto_created == 0
    assert (
        db.query(Part)
        .filter(Part.workspace_id == ws.id, Part.mpn == "EXISTING-MPN")
        .count()
        == 1
    )
    assert _entries(db, project)[0].part_id == part.id


def test_integrity_error_rolls_back_full_import(db, monkeypatch):
    ws, user = _setup_ws(db)
    project = _project(db, ws, user)
    payload = _commit_payload(
        "qty,mpn\n1,FIRST-MPN\n1,SECOND-MPN\n",
        mapping=_mapping("quantity", "mpn"),
    )
    real_flush = db.flush
    flush_calls = 0

    def flaky_flush(*args, **kwargs):
        nonlocal flush_calls
        flush_calls += 1
        if flush_calls == 2:
            raise IntegrityError("boom", {}, Exception("boom"))
        return real_flush(*args, **kwargs)

    monkeypatch.setattr(db, "flush", flaky_flush)

    with pytest.raises(HTTPException) as exc:
        bom.commit(db, workspace_id=ws.id, user_id=user.id, project=project, payload=payload)
    assert exc.value.status_code == 409

    db.rollback()
    assert db.query(Part).filter(Part.workspace_id == ws.id).count() == 0
    assert db.query(ProjectEntry).filter(ProjectEntry.project_id == project.id).count() == 0


def test_workspace_isolation_two_workspaces_same_mpn(db):
    ws_a, user_a = _setup_ws(db, "A")
    ws_b, user_b = _setup_ws(db, "B")
    db.add(
        Part(
            workspace_id=ws_b.id,
            name="Workspace B",
            mpn="SHARED-MPN",
            created_by=user_b.id,
            updated_by=user_b.id,
        )
    )
    project_a = _project(db, ws_a, user_a)
    payload = _commit_payload("qty,mpn\n1,SHARED-MPN\n", mapping=_mapping("quantity", "mpn"))

    result = bom.commit(
        db,
        workspace_id=ws_a.id,
        user_id=user_a.id,
        project=project_a,
        payload=payload,
    )
    db.commit()

    assert result.auto_created == 1
    part_a = db.query(Part).filter(Part.workspace_id == ws_a.id, Part.mpn == "SHARED-MPN").one()
    part_b = db.query(Part).filter(Part.workspace_id == ws_b.id, Part.mpn == "SHARED-MPN").one()
    assert part_a.id != part_b.id
    assert _entries(db, project_a)[0].part_id == part_a.id


def test_preview_would_auto_create_count_matches_commit(db):
    ws, user = _setup_ws(db)
    db.add(
        Part(
            workspace_id=ws.id,
            name="Existing",
            mpn="EXISTING-MPN",
            created_by=user.id,
            updated_by=user.id,
        )
    )
    project = _project(db, ws, user)
    csv_text = "qty,mpn\n1,EXISTING-MPN\n1,NEW-MPN\n2,NEW-MPN\n"
    mapping = _mapping("quantity", "mpn")
    preview_payload = BomImportPreviewIn(
        text_b64=_b64(csv_text),
        separator=",",
        encoding="utf-8",
        has_header=True,
        auto_create_missing_parts=True,
        mapping=mapping,
    )

    preview = bom.preview(preview_payload, db=db, workspace_id=ws.id)
    commit_payload = _commit_payload(csv_text, mapping=mapping)
    result = bom.commit(
        db,
        workspace_id=ws.id,
        user_id=user.id,
        project=project,
        payload=commit_payload,
    )

    assert preview.would_auto_create_count == result.auto_created == 1


def test_preview_would_skip_count_matches_commit(db):
    ws, user = _setup_ws(db)
    project = _project(db, ws, user)
    csv_text = "qty,mpn,part\n1,,\n2,NEW-MPN,\n"
    mapping = _mapping("quantity", "mpn", "part")
    preview_payload = BomImportPreviewIn(
        text_b64=_b64(csv_text),
        separator=",",
        encoding="utf-8",
        has_header=True,
        auto_create_missing_parts=True,
        mapping=mapping,
    )

    preview = bom.preview(preview_payload, db=db, workspace_id=ws.id)
    commit_payload = _commit_payload(csv_text, mapping=mapping)
    result = bom.commit(
        db,
        workspace_id=ws.id,
        user_id=user.id,
        project=project,
        payload=commit_payload,
    )

    assert preview.would_skip_count == result.skipped == 1
