from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import DBAPIError

from app.api._helpers import assert_in_workspace, require_resource_access
from app.api.routes._activity import _DEFAULT_LIMIT, _MAX_LIMIT, build_activity
from app.api.routes._stock_integrity import raise_integrity_as_409
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.errors import ErrorCodes, raise_http
from app.core.responses import ok
from app.core.time import utcnow
from app.domain.audit.service import log as _audit_log
from app.domain.builds.models import Build, BuildStage
from app.domain.builds.schemas import (
    BuildCreateIn,
    BuildPatchIn,
    BuildStageCreateIn,
    ConsumeIn,
    StageConsumeIn,
)
from app.domain.builds.service import (
    BuildError,
    apply_reservations,
    consume,
    release_reservations,
    shortage_analysis,
)
from app.domain.builds.stages import (
    consume_stage,
    create_stage,
    has_consumed_stage,
    has_stages,
    stages_payload,
)
from app.domain.projects.models import Project
from app.domain.stock.models import StockEntry
from app.domain.stock.service import StockConflictError

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
    try:
        return assert_in_workspace(db, Build, bid, ws_id, label="build")
    except HTTPException:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCodes.BUILD_NOT_FOUND,
            message="build not found",
        )


def _get_project(db, ws_id, pid) -> Project:
    try:
        return assert_in_workspace(db, Project, pid, ws_id, label="project")
    except HTTPException:
        raise_http(404, code=ErrorCodes.PROJECT_NOT_FOUND, message="project not found")


def _get_stage(db, ws_id, build: Build, stage_id: UUID) -> BuildStage:
    """Resolve a stage inside the current workspace AND the given build.

    `assert_in_workspace` covers the tenant boundary; the extra `build_id`
    check stops a stage of build A from being consumed through build B's
    URL, which would consume against the wrong BOM allocation.
    """
    try:
        stage = assert_in_workspace(db, BuildStage, stage_id, ws_id, label="build stage")
    except HTTPException:
        raise_http(404, code=ErrorCodes.BUILD_STAGE_NOT_FOUND, message="build stage not found")
    if stage.build_id != build.id or stage.archived_at is not None:
        raise_http(404, code=ErrorCodes.BUILD_STAGE_NOT_FOUND, message="build stage not found")
    return stage


@router.get("")
def list_builds(
    db: DbSession,
    ws: CurrentWorkspace,
    archived: bool = False,
    project_id: UUID | None = None,
    limit: int = Query(default=200, le=1000),
):
    stmt = select(Build).where(Build.workspace_id == ws.id)
    stmt = stmt.where(
        Build.archived_at.is_(None) if not archived else Build.archived_at.is_not(None)
    )
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
    try:
        apply_reservations(
            db, workspace_id=ws.id, user_id=user.id, build=b, project=project
        )
    except DBAPIError as exc:
        raise_integrity_as_409(exc)
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
def patch_build(
    build_id: UUID,
    payload: BuildPatchIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    b = _get_build(db, ws.id, build_id)
    if b.status == "complete" and payload.status != "cancelled":
        raise_http(400, code=ErrorCodes.BUILD_READ_ONLY, message="completed builds are read-only")
    patch_data = payload.model_dump(exclude_unset=True)
    quantity_changed = "quantity" in patch_data and patch_data["quantity"] != b.quantity
    if quantity_changed and has_consumed_stage(db, workspace_id=ws.id, build=b):
        # A quantity change re-derives the whole-build reservation, which
        # would re-reserve material that a completed stage has already
        # consumed. Refuse rather than silently double-count.
        raise_http(
            400,
            code=ErrorCodes.BUILD_READ_ONLY,
            message="cannot change quantity after a stage has been consumed",
        )
    cancelling = patch_data.get("status") == "cancelled" and b.status != "cancelled"
    was_planned_or_in_progress = b.status in ("planned", "in_progress")
    for k, v in patch_data.items():
        setattr(b, k, v)
    b.updated_by = user.id
    db.flush()

    try:
        if cancelling:
            release_reservations(db, workspace_id=ws.id, user_id=user.id, build=b)
        elif (
            quantity_changed
            and was_planned_or_in_progress
            and b.status in ("planned", "in_progress")
        ):
            project = _get_project(db, ws.id, b.project_id)
            release_reservations(db, workspace_id=ws.id, user_id=user.id, build=b)
            apply_reservations(
                db, workspace_id=ws.id, user_id=user.id, build=b, project=project
            )
    except DBAPIError as exc:
        raise_integrity_as_409(exc)

    return ok(_serialize(b))


# Archive/restore — `require_resource_access` enforces resource-existence
# BEFORE the role check (BE2-009). A non-admin probing a foreign
# workspace's build_id gets 404, not 403.
@router.post("/{build_id}/archive")
def archive_build(build_id: UUID, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    b = require_resource_access(
        db, Build, build_id, ws=ws, user=user, role="admin", label="build"
    )
    try:
        release_reservations(db, workspace_id=ws.id, user_id=user.id, build=b)
    except DBAPIError as exc:
        raise_integrity_as_409(exc)
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
    if has_stages(db, workspace_id=ws.id, build=b):
        # A staged build is consumed one stage at a time. Allowing the
        # whole-BOM endpoint here would draw every stage's stock at once
        # while leaving the stages themselves reported as un-built.
        raise_http(
            400,
            code=ErrorCodes.BUILD_HAS_STAGES,
            message="build has stages; consume each stage via /stages/{stage_id}/consume",
        )
    try:
        result = consume(
            db, workspace_id=ws.id, user_id=user.id, build=b, project=project, payload=payload
        )
    except StockConflictError as exc:
        # BE-004 follow-up (#280): the build sub-assembly output is a
        # producer write into the chosen output storage; if that storage
        # is constrained the violation surfaces as a structured 409 with
        # the same body shape as /api/stock/add.
        raise_http(
            status.HTTP_409_CONFLICT,
            code=ErrorCodes.STOCK_CONSTRAINT_VIOLATION,
            message=str(exc),
            constraint=exc.constraint,
            storage_location_id=str(exc.storage_location_id),
        )
    except BuildError as exc:
        # `get_db` rolls back on raise (BE2-010), so dropping the
        # explicit db.rollback() here is safe.
        raise_http(400, code=ErrorCodes.BUILD_CONSUME_ERROR, message=str(exc))
    except DBAPIError as exc:
        raise_integrity_as_409(exc)
    return ok(result)


# --- Multi-stage builds (Track B2) -----------------------------------------
#
# Reservations are taken ONCE, up front, by `POST /api/builds` — creating a
# stage writes no ledger row. Each stage consume releases only its own slice
# of that reservation, so nothing is double-counted across stages. See
# `docs/domain/builds-and-bom.md`.


@router.get("/{build_id}/stages")
def list_build_stages(build_id: UUID, db: DbSession, ws: CurrentWorkspace):
    b = _get_build(db, ws.id, build_id)
    project = _get_project(db, ws.id, b.project_id)
    return ok(stages_payload(db, workspace_id=ws.id, build=b, project=project))


@router.post("/{build_id}/stages", status_code=status.HTTP_201_CREATED)
def create_build_stage(
    build_id: UUID,
    payload: BuildStageCreateIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    b = _get_build(db, ws.id, build_id)
    project = _get_project(db, ws.id, b.project_id)
    try:
        stage = create_stage(
            db,
            workspace_id=ws.id,
            user_id=user.id,
            build=b,
            project=project,
            payload=payload,
        )
    except BuildError as exc:
        raise_http(400, code=ErrorCodes.BUILD_STAGE_ERROR, message=str(exc))
    except DBAPIError as exc:
        raise_integrity_as_409(exc)
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="build_stage.created",
        target_type="build_stage",
        target_ids=[stage.id],
        comment=f"stage '{stage.name}' (sequence {stage.sequence}) on build {b.id}",
    )
    payload_out = stages_payload(db, workspace_id=ws.id, build=b, project=project)
    created = next((s for s in payload_out if s["id"] == str(stage.id)), None)
    return ok(created)


@router.post("/{build_id}/stages/{stage_id}/consume")
def consume_build_stage(
    build_id: UUID,
    stage_id: UUID,
    payload: StageConsumeIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    b = _get_build(db, ws.id, build_id)
    project = _get_project(db, ws.id, b.project_id)
    stage = _get_stage(db, ws.id, b, stage_id)
    try:
        result = consume_stage(
            db,
            workspace_id=ws.id,
            user_id=user.id,
            build=b,
            project=project,
            stage=stage,
            payload=payload,
        )
    except StockConflictError as exc:
        raise_http(
            status.HTTP_409_CONFLICT,
            code=ErrorCodes.STOCK_CONSTRAINT_VIOLATION,
            message=str(exc),
            constraint=exc.constraint,
            storage_location_id=str(exc.storage_location_id),
        )
    except BuildError as exc:
        raise_http(400, code=ErrorCodes.BUILD_CONSUME_ERROR, message=str(exc))
    except DBAPIError as exc:
        raise_integrity_as_409(exc)
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="build_stage.consumed",
        target_type="build_stage",
        target_ids=[stage.id],
        comment=(
            f"stage '{stage.name}' consumed {len(result['consumed_entries'])} lines; "
            f"build now {result['build_status']}"
        ),
    )
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
            raise_http(
                422,
                code=ErrorCodes.ACTIVITY_INVALID_CURSOR,
                message="invalid before_occurred_at",
            )

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
