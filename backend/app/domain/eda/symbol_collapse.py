"""The `symbol-collapse` job: stop one vendor zip minting one schematic
symbol per part.

Every SnapEDA / SamacSys / Ultra Librarian zip a workspace imports ships
its own symbol, and the importer points that part's `part_eda.symbol_id`
at it. Eighty resistors imported from eighty zips is eighty `R` symbols
in the KiCad chooser, all drawing the same two-terminal box. Once the
category seed has put `Device:R` on *Resistors* (plan A6/B3), the
category default is the better answer for all of them — but it never
gets a look in, because `kicad_library.py::_symbol_id_str` resolves
external ref → hosted row → category default, and the hosted row wins.

This job clears `symbol_id` on exactly the rows where that hosted row is
a vendor artefact and the category has a default to fall back to. The
`eda_symbols` rows themselves are left alone: another part may still
point at one, they are the provenance of what was imported, and deleting
library content is not a cleanup this job is entitled to do.

**What it will not touch**

* `symbol_ref_external` — a user typed that, and it outranks the
  category default anyway.
* `source = 'manual'` symbols — hand-uploaded, i.e. someone's own work.
* Parts whose category has no `default_symbol_ref`: clearing there
  leaves the part with no symbol at all, which is strictly worse than a
  redundant one.
* Archived symbols. `_symbol_id_str` already skips them, so the part is
  *already* rendering the category default and there is nothing to
  collapse — listing it would pad the report with rows that change no
  output.
* Footprints. A footprint is per-package and genuinely per-part; only
  the symbol is shared across a class.

Dry run by default, like every other `--apply` job here: the report is a
CSV of every part it would change, with the `symbolIdStr` before and
after, and nothing reaches the database until the operator has read it.

See ADR-0034 and plan item B4.
"""
from __future__ import annotations

import csv
import logging
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TextIO
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session, aliased

from app.domain.audit.service import log as audit_log
from app.domain.categories.models import PartCategory
from app.domain.eda import kicad_refs
from app.domain.eda.models import EdaSymbol, PartEda
from app.domain.parts.models import Part
from app.domain.workspaces.models import Workspace

logger = logging.getLogger(__name__)

__all__ = [
    "AUDIT_ACTION",
    "CSV_COLUMNS",
    "CollapseCandidate",
    "collapse_candidates",
    "run_symbol_collapse",
    "write_report",
]

AUDIT_ACTION = "eda.symbol_collapse"

CSV_COLUMNS = (
    "workspace_id",
    "workspace_name",
    "part_id",
    "part_name",
    "category",
    "symbol_name",
    "symbol_source",
    "before",
    "after",
)


@dataclass(frozen=True)
class CollapseCandidate:
    """One part whose per-part vendor symbol the category default replaces."""

    workspace_id: UUID
    workspace_name: str
    part_id: UUID
    part_name: str
    category_name: str
    symbol_id: UUID
    symbol_name: str
    symbol_source: str
    before: str
    after: str

    def as_row(self) -> dict[str, str]:
        return {
            "workspace_id": str(self.workspace_id),
            "workspace_name": self.workspace_name,
            "part_id": str(self.part_id),
            "part_name": self.part_name,
            "category": self.category_name,
            "symbol_name": self.symbol_name,
            "symbol_source": self.symbol_source,
            "before": self.before,
            "after": self.after,
        }


def collapse_candidates(
    db: Session, *, workspace_id: UUID | None = None
) -> list[CollapseCandidate]:
    """Every part a `--apply` run would change, oldest workspace first.

    One query for the whole estate rather than one per workspace: the
    predicate is the same everywhere and the row count is bounded by
    "parts that have an EDA config at all", which is a few hundred. Each
    join is still workspace-equality-constrained on both sides, so a
    part can never be paired with another workspace's symbol or
    category even if an id somehow crossed over.
    """
    symbol_category = aliased(PartCategory)
    part_category = aliased(PartCategory)

    stmt = (
        select(
            Workspace.id,
            Workspace.name,
            Part.id,
            Part.name,
            part_category.name,
            part_category.default_symbol_ref,
            EdaSymbol.id,
            EdaSymbol.name,
            EdaSymbol.source,
            symbol_category.library_slug,
        )
        .select_from(Part)
        .join(Workspace, Workspace.id == Part.workspace_id)
        .join(
            PartEda,
            and_(
                PartEda.part_id == Part.id,
                PartEda.workspace_id == Part.workspace_id,
            ),
        )
        .join(
            EdaSymbol,
            and_(
                EdaSymbol.id == PartEda.symbol_id,
                EdaSymbol.workspace_id == Part.workspace_id,
                EdaSymbol.archived_at.is_(None),
            ),
        )
        .join(
            part_category,
            and_(
                part_category.id == Part.category_id,
                part_category.workspace_id == Part.workspace_id,
                part_category.archived_at.is_(None),
            ),
        )
        .outerjoin(
            symbol_category,
            and_(
                symbol_category.id == EdaSymbol.category_id,
                symbol_category.workspace_id == Part.workspace_id,
                symbol_category.archived_at.is_(None),
            ),
        )
        .where(Part.archived_at.is_(None))
        .where(EdaSymbol.source != "manual")
        .where(part_category.default_symbol_ref.is_not(None))
        .order_by(Workspace.name.asc(), Part.name.asc(), Part.id.asc())
    )
    if workspace_id is not None:
        stmt = stmt.where(Part.workspace_id == workspace_id)

    return [
        CollapseCandidate(
            workspace_id=row[0],
            workspace_name=row[1],
            part_id=row[2],
            part_name=row[3],
            category_name=row[4],
            symbol_id=row[6],
            symbol_name=row[7],
            symbol_source=row[8],
            before=kicad_refs.symbol_ref(_NamedSymbol(row[7]), row[9]),
            after=row[5],
        )
        for row in db.execute(stmt)
    ]


@dataclass(frozen=True)
class _NamedSymbol:
    """The one attribute `kicad_refs.symbol_ref` reads, without loading
    the whole ORM row just to format a reference string."""

    name: str


def _apply(db: Session, candidates: Sequence[CollapseCandidate]) -> None:
    """Clear `symbol_id` on each candidate's config, plus one audit row
    per workspace.

    One query per workspace loads that workspace's configs, then the ORM
    writes each one. Not a bulk `UPDATE … WHERE part_id IN (…)`: that
    bypasses the identity map, so anything already holding a `PartEda`
    would keep reading the old `symbol_id` until it expired. The
    per-workspace query is what keeps this from being an N+1.

    The `workspace_id` predicate is not redundant with the `part_id`
    list. It is the isolation invariant restated at the write: a
    candidate list that somehow carried a foreign part id must not be
    able to clear that workspace's column.
    """
    by_workspace: dict[UUID, list[CollapseCandidate]] = {}
    for candidate in candidates:
        by_workspace.setdefault(candidate.workspace_id, []).append(candidate)

    for workspace_id, rows in by_workspace.items():
        ws = db.get(Workspace, workspace_id)
        if ws is None:
            continue
        part_ids = [candidate.part_id for candidate in rows]
        configs = db.execute(
            select(PartEda)
            .where(PartEda.workspace_id == workspace_id)
            .where(PartEda.part_id.in_(part_ids))
        ).scalars()
        for config in configs:
            config.symbol_id = None
        db.flush()
        audit_log(
            db,
            ws=ws,
            user=None,
            action=AUDIT_ACTION,
            target_type="part",
            target_ids=part_ids,
            comment=f"collapsed={len(rows)} onto category default symbols",
        )


def write_report(candidates: Iterable[CollapseCandidate], stream: TextIO) -> None:
    """The CSV report. stdout, so it stays separable from the logging."""
    writer = csv.DictWriter(stream, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for candidate in candidates:
        writer.writerow(candidate.as_row())


def run_symbol_collapse(
    db: Session,
    *,
    apply: bool = False,
    workspace_id: UUID | None = None,
    stream: TextIO | None = None,
) -> int:
    """`run_job` entry point. Returns the number of parts collapsed — or,
    in a dry run, the number that would be."""
    candidates = collapse_candidates(db, workspace_id=workspace_id)
    if apply:
        _apply(db, candidates)
    write_report(candidates, stream if stream is not None else sys.stdout)
    logger.info(
        "symbol-collapse apply=%s candidates=%s workspaces=%s",
        apply,
        len(candidates),
        len({candidate.workspace_id for candidate in candidates}),
    )
    return len(candidates)
