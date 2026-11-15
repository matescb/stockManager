"""The `model-placement-fit` job: give existing 3D-model links a placement.

Links made before migration 0084 carry `placement = NULL`, so the KiCad
package still appends their model at the identity — where a Y-up,
self-centred SnapEDA STEP stands on edge inside the board. New links get
a placement when they are made (`model_placement.placement_for_link`);
this backfills the old ones with the same function.

**What it touches:** only `eda_footprint_models.placement`, and only
where it is NULL. A link the footprint's own bytes already place (same
model stem in a `(model …)` node) is reported as `skipped` and left NULL
— the vendor's node wins in the package anyway. STEP links are fitted
first so a same-stem WRL can copy its sibling's result.

**Undo:** set the column back to NULL for the reported link ids. Nothing
else changes, so `requires_report` is False — the schema alone undoes it.

Dry run by default. A dry run still computes every fit (that is the
point of the report) but writes no GLB into the preview cache — a file
in `UPLOAD_DIR` is the one side effect `run_job`'s rollback can't take
back.
"""

from __future__ import annotations

import csv
import logging
import sys
from dataclasses import dataclass
from typing import TextIO
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.domain.audit.service import log as audit_log
from app.domain.eda import model_placement
from app.domain.eda import service as eda_service
from app.domain.eda.models import EdaDatafile, EdaFootprint, EdaFootprintModel
from app.domain.workspaces.models import Workspace

logger = logging.getLogger(__name__)

AUDIT_ACTION = "eda_footprint.model_placement_fitted"
CSV_COLUMNS = (
    "workspace_id",
    "footprint_id",
    "footprint",
    "datafile_id",
    "model",
    "action",
    "detail",
    "placement",
)
WOULD_SET = "would_set"
SET = "set"
SKIPPED = "skipped"
_KIND_ORDER = {"step": 0, "wrl": 1}


@dataclass(frozen=True)
class Outcome:
    workspace_id: UUID
    footprint_id: UUID
    footprint: str
    datafile_id: UUID
    model: str
    action: str
    detail: str = ""
    placement: dict | None = None

    def as_row(self) -> dict[str, str]:
        return {
            "workspace_id": str(self.workspace_id),
            "footprint_id": str(self.footprint_id),
            "footprint": self.footprint,
            "datafile_id": str(self.datafile_id),
            "model": self.model,
            "action": self.action,
            "detail": self.detail,
            "placement": _format(self.placement),
        }


def _format(placement: dict | None) -> str:
    if not placement:
        return ""
    return " ".join(
        f"{key}={','.join(str(v) for v in placement[key])}"
        for key in ("offset", "rotate", "scale")
    )


def _candidates(db: Session, workspace_id: UUID | None) -> list[tuple]:
    """Links with no placement, joined to both parents with a workspace
    equality of their own (ADR-0002); STEP before WRL."""
    stmt = (
        select(EdaFootprintModel, EdaFootprint, EdaDatafile)
        .join(
            EdaFootprint,
            and_(
                EdaFootprint.id == EdaFootprintModel.footprint_id,
                EdaFootprint.workspace_id == EdaFootprintModel.workspace_id,
                EdaFootprint.archived_at.is_(None),
            ),
        )
        .join(
            EdaDatafile,
            and_(
                EdaDatafile.id == EdaFootprintModel.datafile_id,
                EdaDatafile.workspace_id == EdaFootprintModel.workspace_id,
                EdaDatafile.archived_at.is_(None),
            ),
        )
        .where(EdaFootprintModel.placement.is_(None))
        .order_by(EdaFootprintModel.workspace_id, EdaFootprint.name, EdaDatafile.name)
    )
    if workspace_id is not None:
        stmt = stmt.where(EdaFootprintModel.workspace_id == workspace_id)
    rows = list(db.execute(stmt).all())
    rows.sort(key=lambda row: _KIND_ORDER.get(row[2].kind, 2))
    return rows


def _fit_one(db: Session, link, footprint, datafile, *, apply: bool) -> Outcome:
    base = dict(
        workspace_id=link.workspace_id,
        footprint_id=footprint.id,
        footprint=footprint.name,
        datafile_id=datafile.id,
        model=datafile.name,
    )
    node = model_placement.read_footprint(link.workspace_id, footprint)
    if node is None:
        return Outcome(**base, action=SKIPPED, detail="footprint unreadable")
    if model_placement.footprint_places(node, datafile.name):
        return Outcome(**base, action=SKIPPED, detail="footprint places it")
    placement = model_placement.placement_for_link(
        db,
        workspace_id=link.workspace_id,
        footprint=footprint,
        datafile=datafile,
        write_cache=apply,
    )
    if placement is None:
        detail = "no STEP sibling to copy" if datafile.kind == "wrl" else "geometry unreadable"
        return Outcome(**base, action=SKIPPED, detail=detail)
    # Written in a dry run too: `run_job` rolls it back, and a WRL later
    # in this run needs its STEP sibling's placement visible.
    link.placement = placement
    # The link row has no timestamp; the footprint's `updated_at` is what
    # the PCM package version is derived from. Without the touch the
    # archive is rebuilt but no installed KiCad is offered the update.
    eda_service.touch_footprint(footprint, None)
    db.flush()
    return Outcome(**base, action=SET if apply else WOULD_SET, placement=placement)


def _audit(db: Session, outcomes: list[Outcome]) -> None:
    by_workspace: dict[UUID, list[Outcome]] = {}
    for outcome in outcomes:
        if outcome.action == SET:
            by_workspace.setdefault(outcome.workspace_id, []).append(outcome)
    for workspace_id, rows in by_workspace.items():
        ws = db.get(Workspace, workspace_id)
        if ws is None:
            continue
        audit_log(
            db,
            ws=ws,
            user=None,
            action=AUDIT_ACTION,
            target_type="eda_footprint",
            target_ids=sorted({row.footprint_id for row in rows}),
            comment=f"model placements fitted={len(rows)}",
        )


def run_model_placement_fit(
    db: Session,
    *,
    apply: bool = False,
    workspace_id: UUID | None = None,
    stream: TextIO | None = None,
) -> int:
    """`run_job` entry point. Returns the number of links set (or that
    would be, in a dry run)."""
    if workspace_id is not None and db.get(Workspace, workspace_id) is None:
        raise LookupError(f"no workspace with id {workspace_id}")
    outcomes = [
        _fit_one(db, link, footprint, datafile, apply=apply)
        for link, footprint, datafile in _candidates(db, workspace_id)
    ]
    if apply:
        _audit(db, outcomes)
    writer = csv.DictWriter(
        stream if stream is not None else sys.stdout,
        fieldnames=list(CSV_COLUMNS),
        lineterminator="\n",
    )
    writer.writeheader()
    for outcome in outcomes:
        writer.writerow(outcome.as_row())
    affected = sum(1 for o in outcomes if o.action in (WOULD_SET, SET))
    logger.info(
        "model-placement-fit apply=%s affected=%s reported=%s", apply, affected, len(outcomes)
    )
    return affected
