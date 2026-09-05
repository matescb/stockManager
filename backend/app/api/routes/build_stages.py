"""Multi-stage build routes (Track B2), mounted under `/api/builds`.

Split out of `routes/builds.py` rather than appended to it: that module
carries a 300-line CI budget (`line-count-budget` in `.github/workflows/ci.yml`,
CQ-002/CQ-003) and the stage surface would have blown through it. Same
pattern as the `parts_core` / `parts_scan` / `parts_assets` split.

The build and project lookups are imported from `routes/builds.py`; the
dependency only goes that way, so there is no import cycle.

Reservations are taken ONCE, up front, by `POST /api/builds` — creating a
stage writes no ledger row. Each stage consume releases only its own slice
of that reservation, so nothing is double-counted across stages. See
`docs/domain/builds-and-bom.md`.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import DBAPIError

from app.api._helpers import assert_in_workspace
from app.api.routes._stock_integrity import raise_integrity_as_409
from app.api.routes.builds import _get_build, _get_project
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.errors import ErrorCodes, raise_http
from app.core.responses import ok
from app.domain.audit.service import log as _audit_log
from app.domain.builds.models import Build, BuildStage
from app.domain.builds.schemas import BuildStageCreateIn, StageConsumeIn
from app.domain.builds.service import BuildError
from app.domain.builds.stages import consume_stage, create_stage, stages_payload
from app.domain.stock.service import StockConflictError

router = APIRouter()


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
        # Same 409 contract as the whole-build consume: the sub-assembly
        # output on the final stage is a producer write into the chosen
        # storage, so single_part_only / existing_parts_only apply.
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
