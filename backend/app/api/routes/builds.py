from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.responses import ok
from app.domain.builds.models import Build
from app.domain.builds.schemas import BuildCreateIn, BuildPatchIn, ConsumeIn
from app.domain.builds.service import BuildError, consume, shortage_analysis
from app.domain.projects.models import Project

router = APIRouter()


def _serialize(b: Build) -> dict:
    return {
        "id": str(b.id),
        "name": b.name,
        "project_id": str(b.project_id),
        "quantity": b.quantity,
        "status": b.status,
        "started_at": b.started_at.isoformat() if b.started_at else None,
        "completed_at": b.completed_at.isoformat() if b.completed_at else None,
        "output_lot_id": str(b.output_lot_id) if b.output_lot_id else None,
        "comments": b.comments,
        "archived_at": b.archived_at.isoformat() if b.archived_at else None,
        "created_at": b.created_at.isoformat(),
        "updated_at": b.updated_at.isoformat(),
    }


def _get_build(db, ws_id, bid) -> Build:
    b = db.get(Build, bid)
    if not b or b.workspace_id != ws_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="build not found")
    return b


def _get_project(db, ws_id, pid) -> Project:
    p = db.get(Project, pid)
    if not p or p.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail="project not found")
    return p


@router.get("")
def list_builds(db: DbSession, ws: CurrentWorkspace, archived: bool = False, project_id: UUID | None = None):
    stmt = select(Build).where(Build.workspace_id == ws.id)
    stmt = stmt.where(Build.archived_at.is_(None) if not archived else Build.archived_at.is_not(None))
    if project_id:
        stmt = stmt.where(Build.project_id == project_id)
    stmt = stmt.order_by(Build.created_at.desc())
    return ok([_serialize(b) for b in db.execute(stmt).scalars()])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_build(payload: BuildCreateIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    project = _get_project(db, ws.id, payload.project_id)
    b = Build(
        workspace_id=ws.id,
        name=payload.name,
        project_id=project.id,
        quantity=payload.quantity,
        comments=payload.comments,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(b)
    db.commit()
    return ok(_serialize(b))


@router.get("/{build_id}")
def get_build(build_id: UUID, db: DbSession, ws: CurrentWorkspace):
    b = _get_build(db, ws.id, build_id)
    project = _get_project(db, ws.id, b.project_id)
    return ok(
        {
            "build": _serialize(b),
            "shortage": shortage_analysis(
                db, workspace_id=ws.id, project=project, build_quantity=b.quantity
            ),
        }
    )


@router.patch("/{build_id}")
def patch_build(build_id: UUID, payload: BuildPatchIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    b = _get_build(db, ws.id, build_id)
    if b.status == "complete" and payload.status != "cancelled":
        raise HTTPException(status_code=400, detail="completed builds are read-only")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(b, k, v)
    b.updated_by = user.id
    db.commit()
    return ok(_serialize(b))


@router.post("/{build_id}/archive")
def archive_build(build_id: UUID, db: DbSession, ws: CurrentWorkspace):
    b = _get_build(db, ws.id, build_id)
    b.archived_at = datetime.now(timezone.utc)
    db.commit()
    return ok(None, "archived")


@router.post("/{build_id}/restore")
def restore_build(build_id: UUID, db: DbSession, ws: CurrentWorkspace):
    b = _get_build(db, ws.id, build_id)
    b.archived_at = None
    db.commit()
    return ok(None, "restored")


@router.post("/{build_id}/consume")
def consume_build(
    build_id: UUID, payload: ConsumeIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser
):
    b = _get_build(db, ws.id, build_id)
    project = _get_project(db, ws.id, b.project_id)
    try:
        result = consume(
            db, workspace_id=ws.id, user_id=user.id, build=b, project=project, payload=payload
        )
        db.commit()
    except BuildError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    return ok(result)
