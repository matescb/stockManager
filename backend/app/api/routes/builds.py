from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import and_, or_, select

from app.api._helpers import require_resource_access
from app.api.routes._activity import _DEFAULT_LIMIT, _MAX_LIMIT, build_activity
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.responses import ok
from app.core.time import utcnow
from app.domain.builds.models import Build
from app.domain.builds.schemas import BuildCreateIn, BuildPatchIn, ConsumeIn
from app.domain.builds.service import (
    BuildError,
    apply_reservations,
    consume,
    release_reservations,
    shortage_analysis,
)
from app.domain.projects.models import Project
from app.domain.stock.models import StockEntry

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
def list_builds(
    db: DbSession,
    ws: CurrentWorkspace,
    archived: bool = False,
    project_id: UUID | None = None,
    limit: int = Query(default=200, le=1000),
):
    stmt = select(Build).where(Build.workspace_id == ws.id)
    stmt = stmt.where(Build.archived_at.is_(None) if not archived else Build.archived_at.is_not(None))
    if project_id:
        stmt = stmt.where(Build.project_id == project_id)
    stmt = stmt.order_by(Build.created_at.desc()).limit(limit)
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
    db.flush()
    # `get_db` rolls back on any raised exception (BE2-010), so the
    # explicit try/except db.rollback() shape becomes redundant here.
    apply_reservations(
        db, workspace_id=ws.id, user_id=user.id, build=b, project=project
    )
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
    patch_data = payload.model_dump(exclude_unset=True)
    quantity_changed = "quantity" in patch_data and patch_data["quantity"] != b.quantity
    cancelling = patch_data.get("status") == "cancelled" and b.status != "cancelled"
    was_planned_or_in_progress = b.status in ("planned", "in_progress")
    for k, v in patch_data.items():
        setattr(b, k, v)
    b.updated_by = user.id
    db.flush()

    if cancelling:
        release_reservations(db, workspace_id=ws.id, user_id=user.id, build=b)
    elif quantity_changed and was_planned_or_in_progress and b.status in ("planned", "in_progress"):
        project = _get_project(db, ws.id, b.project_id)
        release_reservations(db, workspace_id=ws.id, user_id=user.id, build=b)
        apply_reservations(
            db, workspace_id=ws.id, user_id=user.id, build=b, project=project
        )

    return ok(_serialize(b))


# Archive/restore — `require_resource_access` enforces resource-existence
# BEFORE the role check (BE2-009). A non-admin probing a foreign
# workspace's build_id gets 404, not 403.
@router.post("/{build_id}/archive")
def archive_build(build_id: UUID, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    b = require_resource_access(
        db, Build, build_id, ws=ws, user=user, role="admin", label="build"
    )
    release_reservations(db, workspace_id=ws.id, user_id=user.id, build=b)
    b.archived_at = utcnow()
    return ok(None, "archived")


@router.post("/{build_id}/restore")
def restore_build(build_id: UUID, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    b = require_resource_access(
        db, Build, build_id, ws=ws, user=user, role="admin", label="build"
    )
    b.archived_at = None
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
    except BuildError as exc:
        # `get_db` rolls back on raise (BE2-010), so dropping the
        # explicit db.rollback() here is safe.
        raise HTTPException(status_code=400, detail=str(exc))
    return ok(result)


@router.get("/{build_id}/activity")
def build_activity_route(
    request: Request,
    build_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    before_occurred_at: str | None = Query(default=None),
    before_id: UUID | None = Query(default=None),
):
    b = _get_build(db, ws.id, build_id)

    cursor_at: datetime | None = None
    if before_occurred_at is not None:
        try:
            cursor_at = datetime.fromisoformat(before_occurred_at)
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid before_occurred_at")

    stmt = (
        select(StockEntry)
        .where(StockEntry.workspace_id == ws.id)
        .where(StockEntry.build_id == b.id)
    )
    if cursor_at is not None and before_id is not None:
        stmt = stmt.where(
            or_(
                StockEntry.occurred_at < cursor_at,
                and_(
                    StockEntry.occurred_at == cursor_at,
                    StockEntry.id < before_id,
                ),
            )
        )
    stmt = stmt.order_by(StockEntry.occurred_at.desc(), StockEntry.id.desc()).limit(limit + 1)
    stock_rows = list(db.execute(stmt).scalars())

    if not hasattr(request.state, "user_cache"):
        request.state.user_cache = {}

    result = build_activity(
        db,
        stock_rows=stock_rows,
        created_at=b.created_at,
        updated_at=b.updated_at,
        created_by=b.created_by,
        updated_by=b.updated_by,
        created_kind="build_created",
        updated_kind="build_updated",
        limit=limit,
        include_synthetic=(cursor_at is None),
        user_cache=request.state.user_cache,
    )
    return ok(result)
