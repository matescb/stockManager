"""Kitting routes (Track B3), mounted under `/api/builds`.

Own module rather than appended to `routes/builds.py` for the same reason
`build_stages.py` is: that module carries a 300-line CI budget
(`line-count-budget` in `.github/workflows/ci.yml`, CQ-002/CQ-003) and is
already within two lines of it. The build / project / stage lookups are
imported from the two modules that own them; the dependency only goes that
way, so there is no import cycle.

Four routes, two shapes:

* `GET  …/kit-plan`  — read-only preview: what would move, from where, and
  what the kit would fall short of.
* `POST …/kit`       — do it, returning the same body with `executed: true`.

each in a whole-build and a per-stage flavour. Domain rules (a kit is a
move; `_required` is the only quantity authority; the kit *tops up* the
staging location; reservations are untouched) live in
`domain/builds/kitting.py`.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy.exc import DBAPIError

from app.api.routes._stock_integrity import raise_integrity_as_409
from app.api.routes.build_stages import _get_stage
from app.api.routes.builds import _get_build, _get_project
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.errors import ErrorCodes, raise_http
from app.core.responses import ok
from app.domain.audit.service import log as _audit_log
from app.domain.builds.kitting import (
    execute_kit,
    plan_kit,
    resolve_staging,
    serialize_kit,
)
from app.domain.builds.models import Build, BuildStage
from app.domain.builds.schemas import KitIn
from app.domain.builds.service import BuildError
from app.domain.builds.stages import has_stages
from app.domain.stock.service import StockConflictError, StockError

router = APIRouter()


def _refuse_staged_whole_build(db, ws_id, build: Build) -> None:
    """A staged build is kitted stage by stage.

    Same guard, and the same reasoning, as `POST /{build_id}/consume`: the
    whole-BOM quantity is the sum of every stage's slice, so a whole-build
    kit of a partly-consumed staged build would haul material for stages
    that already drew their stock.
    """
    if has_stages(db, workspace_id=ws_id, build=build):
        raise_http(
            400,
            code=ErrorCodes.BUILD_HAS_STAGES,
            message="build has stages; kit each stage via /stages/{stage_id}/kit",
        )


def _kit_response(
    db,
    ws,
    user,
    *,
    build: Build,
    stage: BuildStage | None,
    storage_location_id: UUID,
    execute: bool,
) -> dict:
    """Shared body of all four routes: resolve, plan or execute, serialise."""
    project = _get_project(db, ws.id, build.project_id)
    try:
        staging = resolve_staging(
            db, workspace_id=ws.id, storage_location_id=storage_location_id
        )
        if execute:
            lines = execute_kit(
                db,
                workspace_id=ws.id,
                user_id=user.id,
                build=build,
                project=project,
                stage=stage,
                staging=staging,
            )
        else:
            lines = plan_kit(
                db,
                workspace_id=ws.id,
                build=build,
                project=project,
                stage=stage,
                staging=staging,
            )
    except StockConflictError as exc:
        # The staging location is a producer destination, so
        # single_part_only / existing_parts_only apply to it exactly as
        # they do to /api/stock/move. Same 409 body shape.
        raise_http(
            status.HTTP_409_CONFLICT,
            code=ErrorCodes.STOCK_CONSTRAINT_VIOLATION,
            message=str(exc),
            constraint=exc.constraint,
            storage_location_id=str(exc.storage_location_id),
        )
    except (BuildError, StockError) as exc:
        raise_http(400, code=ErrorCodes.BUILD_KIT_ERROR, message=str(exc))
    except DBAPIError as exc:
        raise_integrity_as_409(exc)

    body = serialize_kit(
        db,
        workspace_id=ws.id,
        build=build,
        stage=stage,
        staging=staging,
        lines=lines,
        executed=execute,
    )
    if execute:
        _audit_log(
            db,
            ws=ws,
            user=user,
            action="build.kitted",
            target_type="build",
            target_ids=[build.id] + ([stage.id] if stage else []),
            comment=(
                f"kitted {body['totals']['moving']} units across "
                f"{body['totals']['lines']} part(s) to '{staging.name}'; "
                f"{body['totals']['short_lines']} short"
            ),
        )
    return body


@router.get("/{build_id}/kit-plan")
def build_kit_plan(
    build_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
    storage_location_id: UUID = Query(...),
):
    b = _get_build(db, ws.id, build_id)
    _refuse_staged_whole_build(db, ws.id, b)
    return ok(
        _kit_response(
            db, ws, user,
            build=b, stage=None,
            storage_location_id=storage_location_id, execute=False,
        )
    )


@router.post("/{build_id}/kit")
def build_kit(
    build_id: UUID,
    payload: KitIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    b = _get_build(db, ws.id, build_id)
    _refuse_staged_whole_build(db, ws.id, b)
    return ok(
        _kit_response(
            db, ws, user,
            build=b, stage=None,
            storage_location_id=payload.storage_location_id, execute=True,
        )
    )


@router.get("/{build_id}/stages/{stage_id}/kit-plan")
def build_stage_kit_plan(
    build_id: UUID,
    stage_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
    storage_location_id: UUID = Query(...),
):
    b = _get_build(db, ws.id, build_id)
    stage = _get_stage(db, ws.id, b, stage_id)
    return ok(
        _kit_response(
            db, ws, user,
            build=b, stage=stage,
            storage_location_id=storage_location_id, execute=False,
        )
    )


@router.post("/{build_id}/stages/{stage_id}/kit")
def build_stage_kit(
    build_id: UUID,
    stage_id: UUID,
    payload: KitIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    b = _get_build(db, ws.id, build_id)
    stage = _get_stage(db, ws.id, b, stage_id)
    return ok(
        _kit_response(
            db, ws, user,
            build=b, stage=stage,
            storage_location_id=payload.storage_location_id, execute=True,
        )
    )
