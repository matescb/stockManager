"""Printable pick-list routes (Track B4), mounted under `/api/builds`.

Its own module for the same reason `build_stages.py` is: `routes/builds.py`
carries a 300-line CI budget (`line-count-budget` in
`.github/workflows/ci.yml`, CQ-002/CQ-003) and is already at 298 lines, so
new routes go beside it rather than into it.

The build / project / stage lookups are imported from `routes/builds.py`
and `routes/build_stages.py`; the dependency only goes that way, so there
is no import cycle.

Both routes are **read-only** — they emit no ledger row, touch no
reservation, and therefore write no `audit_log` row. CLAUDE.md's universal
audit invariant covers workspace *mutations*; a GET that renders a sheet
is not one, and logging every print would bury the mutation trail the
invariant exists to protect.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.api.routes.build_stages import _get_stage
from app.api.routes.builds import _get_build, _get_project
from app.core.deps import CurrentWorkspace, DbSession
from app.core.responses import ok
from app.domain.builds.picklist import pick_list

router = APIRouter()


@router.get("/{build_id}/pick-list")
def build_pick_list(build_id: UUID, db: DbSession, ws: CurrentWorkspace):
    """Whole-build pick sheet: every consumable BOM line at its full
    `_required` quantity, with the shelf walk to collect it."""
    build = _get_build(db, ws.id, build_id)
    project = _get_project(db, ws.id, build.project_id)
    return ok(pick_list(db, workspace_id=ws.id, build=build, project=project))


@router.get("/{build_id}/stages/{stage_id}/pick-list")
def build_stage_pick_list(
    build_id: UUID, stage_id: UUID, db: DbSession, ws: CurrentWorkspace
):
    """Per-stage pick sheet: only the BOM lines this stage covers, each at
    the stage's slice of `_required`.

    A staged build's picker wants this stage's parts and nothing else —
    handing them the whole-build sheet would have them fetch material the
    next stage needs and leave it on the bench.
    """
    build = _get_build(db, ws.id, build_id)
    project = _get_project(db, ws.id, build.project_id)
    stage = _get_stage(db, ws.id, build, stage_id)
    return ok(
        pick_list(db, workspace_id=ws.id, build=build, project=project, stage=stage)
    )
