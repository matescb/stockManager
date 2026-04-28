from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select

from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.responses import ok
from app.domain.projects import bom_import as bom
from app.domain.projects.models import Project, ProjectEntry
from app.domain.projects.schemas import (
    BomEntryIn,
    BomEntryPatch,
    BomImportCommitIn,
    BomImportPreviewIn,
    ProjectCreateIn,
    ProjectPatchIn,
)

router = APIRouter()


def _serialize(p: Project) -> dict:
    return {
        "id": str(p.id),
        "name": p.name,
        "description": p.description,
        "notes_markdown": p.notes_markdown,
        "associated_subassembly_part_id": str(p.associated_subassembly_part_id) if p.associated_subassembly_part_id else None,
        "archived_at": p.archived_at.isoformat() if p.archived_at else None,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


def _serialize_entry(e: ProjectEntry) -> dict:
    return {
        "id": str(e.id),
        "project_id": str(e.project_id),
        "entry_type": e.entry_type,
        "part_id": str(e.part_id) if e.part_id else None,
        "meta_part_id": str(e.meta_part_id) if e.meta_part_id else None,
        "name": e.name,
        "quantity": float(e.quantity) if e.quantity is not None else 0,
        "comments": e.comments,
        "designators": e.designators or [],
        "cad_footprint": e.cad_footprint,
        "cad_key": e.cad_key,
        "dnp": e.dnp,
        "order_index": e.order_index,
    }


@router.get("")
def list_projects(db: DbSession, ws: CurrentWorkspace, archived: bool = False, q: str | None = None):
    stmt = select(Project).where(Project.workspace_id == ws.id)
    stmt = stmt.where(Project.archived_at.is_(None) if not archived else Project.archived_at.is_not(None))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Project.name.ilike(like), Project.description.ilike(like)))
    stmt = stmt.order_by(Project.updated_at.desc())
    return ok([_serialize(p) for p in db.execute(stmt).scalars()])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreateIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    p = Project(
        workspace_id=ws.id,
        name=payload.name,
        description=payload.description,
        notes_markdown=payload.notes_markdown,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(p)
    db.commit()
    return ok(_serialize(p))


def _get(db, ws_id, pid) -> Project:
    p = db.get(Project, pid)
    if not p or p.workspace_id != ws_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return p


@router.get("/{project_id}")
def get_project(project_id: UUID, db: DbSession, ws: CurrentWorkspace):
    return ok(_serialize(_get(db, ws.id, project_id)))


@router.patch("/{project_id}")
def patch_project(project_id: UUID, payload: ProjectPatchIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    p = _get(db, ws.id, project_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    p.updated_by = user.id
    db.commit()
    return ok(_serialize(p))


@router.post("/{project_id}/archive")
def archive_project(project_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get(db, ws.id, project_id)
    p.archived_at = datetime.now(timezone.utc)
    db.commit()
    return ok(None, "archived")


@router.post("/{project_id}/restore")
def restore_project(project_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get(db, ws.id, project_id)
    p.archived_at = None
    db.commit()
    return ok(None, "restored")


# --------- BOM ----------
@router.get("/{project_id}/entries")
def list_entries(project_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get(db, ws.id, project_id)
    rows = list(
        db.execute(
            select(ProjectEntry).where(ProjectEntry.workspace_id == ws.id).where(ProjectEntry.project_id == p.id).order_by(ProjectEntry.order_index)
        ).scalars()
    )
    return ok([_serialize_entry(e) for e in rows])


@router.post("/{project_id}/entries")
def add_entry(project_id: UUID, payload: BomEntryIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    p = _get(db, ws.id, project_id)
    next_idx = (
        db.execute(
            select(ProjectEntry.order_index)
            .where(ProjectEntry.workspace_id == ws.id)
            .where(ProjectEntry.project_id == p.id)
            .order_by(ProjectEntry.order_index.desc())
            .limit(1)
        ).scalar() or 0
    ) + 1
    e = ProjectEntry(
        workspace_id=ws.id,
        project_id=p.id,
        entry_type=payload.entry_type,
        part_id=payload.part_id,
        meta_part_id=payload.meta_part_id,
        name=payload.name or "",
        quantity=payload.quantity,
        comments=payload.comments,
        designators=payload.designators or [],
        cad_footprint=payload.cad_footprint,
        cad_key=payload.cad_key,
        dnp=payload.dnp,
        order_index=next_idx,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(e)
    db.commit()
    return ok(_serialize_entry(e))


@router.patch("/{project_id}/entries/{entry_id}")
def patch_entry(project_id: UUID, entry_id: UUID, payload: BomEntryPatch, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    p = _get(db, ws.id, project_id)
    e = db.get(ProjectEntry, entry_id)
    if not e or e.workspace_id != ws.id or e.project_id != p.id:
        raise HTTPException(status_code=404, detail="entry not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(e, k, v)
    e.updated_by = user.id
    db.commit()
    return ok(_serialize_entry(e))


@router.delete("/{project_id}/entries/{entry_id}")
def del_entry(project_id: UUID, entry_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get(db, ws.id, project_id)
    e = db.get(ProjectEntry, entry_id)
    if not e or e.workspace_id != ws.id or e.project_id != p.id:
        raise HTTPException(status_code=404, detail="entry not found")
    db.delete(e)
    db.commit()
    return ok(None)


# --------- BOM import ----------
@router.post("/{project_id}/bom/preview")
def preview_bom(project_id: UUID, payload: BomImportPreviewIn, db: DbSession, ws: CurrentWorkspace):
    _get(db, ws.id, project_id)
    return ok(bom.preview(payload).model_dump())


@router.post("/{project_id}/bom/import")
def commit_bom(project_id: UUID, payload: BomImportCommitIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    project = _get(db, ws.id, project_id)
    try:
        result = bom.commit(db, workspace_id=ws.id, user_id=user.id, project=project, payload=payload)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(result.model_dump())


class MatchEntryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_id: UUID


@router.post("/{project_id}/entries/{entry_id}/match")
def match_entry(project_id: UUID, entry_id: UUID, payload: MatchEntryIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    from app.domain.parts.models import Part
    p = _get(db, ws.id, project_id)
    e = db.get(ProjectEntry, entry_id)
    if not e or e.workspace_id != ws.id or e.project_id != p.id:
        raise HTTPException(status_code=404, detail="entry not found")
    part = db.get(Part, payload.part_id)
    if not part or part.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="part not found")
    e.part_id = part.id
    e.entry_type = "part"
    e.updated_by = user.id
    db.commit()
    return ok(_serialize_entry(e))
