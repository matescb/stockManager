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

Clearing leaves both `symbol_id` and `symbol_ref_external` NULL, which
`ck_part_eda_symbol_ref_exclusive` permits — that pair means "inherit
the category default", which is the whole point.

**What it will not touch**

* `symbol_ref_external` — a user typed that, and it outranks the
  category default anyway.
* `source = 'manual'` symbols — hand-uploaded, i.e. someone's own work.
* Parts whose category has no usable `default_symbol_ref`. "Usable" is
  a non-empty string, not merely NOT NULL: the consumer at
  `kicad_library.py:298` tests the column for truthiness, so a category
  patched to `""` (the REST and MCP schemas both accept it) resolves to
  no symbol at all. Clearing there would delete those parts from the
  KiCad library outright, because `_document` returns None for a part
  with no symbol. The predicate is `coalesce(…, '') != ''` for that
  reason and must stay that way.
* Archived symbols. `_symbol_id_str` already skips them, so the part is
  *already* rendering the category default and there is nothing to
  collapse — listing it would pad the report with rows that change no
  output.
* Footprints. A footprint is per-package and genuinely per-part; only
  the symbol is shared across a class.
* A config that changed since the candidate list was built. The
  candidates are read, the operator is shown a report, and the write
  happens later in the same run; a `PUT /parts/{id}/eda` in between is
  a user decision, and overwriting it silently is exactly what this job
  must not do.

Dry run by default, like every other `--apply` job here: the report is a
CSV of every part it would change, with the `symbolIdStr` before and
after, and nothing reaches the database until the operator has read it.

See ADR-0034 and plan item B4.
"""
from __future__ import annotations

import csv
import logging
import sys
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from typing import TextIO
from uuid import UUID

from sqlalchemy import Select, and_, func, select
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
    "CLEARED",
    "CSV_COLUMNS",
    "REASON_CHANGED",
    "SKIPPED",
    "WOULD_CLEAR",
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
    "action",
    "detail",
)

# What `CollapseCandidate.action` can be.
WOULD_CLEAR = "would_clear"
CLEARED = "cleared"
SKIPPED = "skipped"

REASON_CHANGED = "the part's EDA config changed during the run; re-run the job"

# Postgres has no hard cap on an `IN (…)` list, but a multi-thousand-element
# one is a parse-time cost and an unreadable log line. Candidates are
# chunked at this width; a prod workspace has a few hundred configured
# parts, so in practice this is one statement.
_IN_CHUNK = 1000


@dataclass(frozen=True)
class CollapseCandidate:
    """One part whose per-part vendor symbol the category default replaces.

    `action` starts as `would_clear` — what a dry run reports — and
    `_apply` stamps the real outcome onto a copy.
    """

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
    action: str = WOULD_CLEAR
    detail: str = ""

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
            "action": self.action,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class _NamedSymbol:
    """The one attribute `kicad_refs.symbol_ref` reads, without loading
    the whole ORM row just to format a reference string."""

    name: str


def _candidates_stmt(workspace_id: UUID | None) -> Select:
    """Every part a `--apply` run would change.

    One query for the whole estate rather than one per workspace: the
    predicate is the same everywhere and the row count is bounded by
    "parts that have an EDA config at all". Every join is
    workspace-equality-constrained on both sides, so a part can never be
    paired with another workspace's symbol or category even if an id
    somehow crossed over.
    """
    symbol_category = aliased(PartCategory)
    part_category = aliased(PartCategory)

    stmt = (
        select(
            Workspace.id.label("workspace_id"),
            Workspace.name.label("workspace_name"),
            Part.id.label("part_id"),
            Part.name.label("part_name"),
            part_category.name.label("category_name"),
            part_category.default_symbol_ref.label("default_symbol_ref"),
            EdaSymbol.id.label("symbol_id"),
            EdaSymbol.name.label("symbol_name"),
            EdaSymbol.source.label("symbol_source"),
            symbol_category.library_slug.label("symbol_category_slug"),
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
        # NOT `is_not(None)`: an empty string resolves to no symbol at all
        # downstream, and clearing against it would delete the part from
        # the KiCad library. See the module docstring.
        .where(func.coalesce(part_category.default_symbol_ref, "") != "")
        .order_by(Workspace.name.asc(), Part.name.asc(), Part.id.asc())
    )
    if workspace_id is not None:
        stmt = stmt.where(Part.workspace_id == workspace_id)
    return stmt


def _row_to_candidate(row) -> CollapseCandidate:
    return CollapseCandidate(
        workspace_id=row.workspace_id,
        workspace_name=row.workspace_name,
        part_id=row.part_id,
        part_name=row.part_name,
        category_name=row.category_name,
        symbol_id=row.symbol_id,
        symbol_name=row.symbol_name,
        symbol_source=row.symbol_source,
        before=kicad_refs.symbol_ref(
            _NamedSymbol(row.symbol_name), row.symbol_category_slug
        ),
        after=row.default_symbol_ref,
    )


def assert_workspace_exists(db: Session, workspace_id: UUID | None) -> None:
    """Refuse a `--workspace` that names no workspace.

    Without this the job reports a header-only CSV and exits 0, which
    reads as "there was nothing to do" — the one answer an operator must
    not be given for a typo in a UUID.
    """
    if workspace_id is None:
        return
    if db.get(Workspace, workspace_id) is None:
        raise LookupError(f"no workspace with id {workspace_id}")


def collapse_candidates(
    db: Session, *, workspace_id: UUID | None = None
) -> list[CollapseCandidate]:
    """Every part a `--apply` run would change, by workspace then name."""
    assert_workspace_exists(db, workspace_id)
    return [_row_to_candidate(row) for row in db.execute(_candidates_stmt(workspace_id))]


def _chunks(items: Sequence[UUID], size: int = _IN_CHUNK) -> Iterator[Sequence[UUID]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _configs_for(
    db: Session, *, workspace_id: UUID, part_ids: Sequence[UUID]
) -> list[PartEda]:
    """One workspace's `part_eda` rows for the given parts.

    Chunked rather than one unbounded `IN (…)`, and one query per chunk
    rather than one per part — the per-part version was the N+1 this
    helper exists to avoid. The `workspace_id` predicate is not
    redundant with the id list: it is the isolation invariant restated
    at the write, so a candidate list carrying a foreign part id could
    still not reach another workspace's row.
    """
    rows: list[PartEda] = []
    for chunk in _chunks(part_ids):
        rows += list(
            db.execute(
                select(PartEda)
                .where(PartEda.workspace_id == workspace_id)
                .where(PartEda.part_id.in_(chunk))
            ).scalars()
        )
    return rows


def _apply_one_workspace(
    db: Session, *, ws: Workspace, rows: Sequence[CollapseCandidate]
) -> list[CollapseCandidate]:
    """Clear `symbol_id` for one workspace, returning stamped outcomes.

    Each config is re-checked against the symbol the candidate was built
    from. A `PUT /parts/{id}/eda` between the read and this write is a
    user decision, and a deleted config means the part no longer has one
    at all; both are reported rather than overwritten. The audit row and
    the run's return value are built from what was actually cleared, so
    neither can over-claim.
    """
    expected = {row.part_id: row.symbol_id for row in rows}
    configs = {
        config.part_id: config
        for config in _configs_for(
            db, workspace_id=ws.id, part_ids=[row.part_id for row in rows]
        )
    }

    outcomes: list[CollapseCandidate] = []
    cleared: list[CollapseCandidate] = []
    for row in rows:
        config = configs.get(row.part_id)
        if config is None or config.symbol_id != expected[row.part_id]:
            outcomes.append(replace(row, action=SKIPPED, detail=REASON_CHANGED))
            continue
        config.symbol_id = None
        stamped = replace(row, action=CLEARED)
        outcomes.append(stamped)
        cleared.append(stamped)

    if not cleared:
        return outcomes

    db.flush()
    audit_log(
        db,
        ws=ws,
        user=None,
        action=AUDIT_ACTION,
        target_type="part",
        target_ids=[row.part_id for row in cleared],
        comment=f"collapsed={len(cleared)} onto category default symbols",
    )
    return outcomes


def _apply(
    db: Session, candidates: Sequence[CollapseCandidate]
) -> list[CollapseCandidate]:
    """Apply every workspace's candidates, returning stamped outcomes."""
    by_workspace: dict[UUID, list[CollapseCandidate]] = {}
    for candidate in candidates:
        by_workspace.setdefault(candidate.workspace_id, []).append(candidate)

    outcomes: list[CollapseCandidate] = []
    for workspace_id, rows in by_workspace.items():
        ws = db.get(Workspace, workspace_id)
        if ws is None:
            outcomes += [
                replace(row, action=SKIPPED, detail="workspace is gone") for row in rows
            ]
            continue
        outcomes += _apply_one_workspace(db, ws=ws, rows=rows)
    return outcomes


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
    """`run_job` entry point.

    Returns the number of parts actually cleared — or, in a dry run, the
    number that would be. A part the write pass found changed is in the
    report as `skipped` and is not counted.
    """
    outcomes = collapse_candidates(db, workspace_id=workspace_id)
    if apply:
        outcomes = _apply(db, outcomes)
    write_report(outcomes, stream if stream is not None else sys.stdout)
    affected = sum(1 for row in outcomes if row.action in (WOULD_CLEAR, CLEARED))
    logger.info(
        "symbol-collapse apply=%s affected=%s reported=%s workspaces=%s",
        apply,
        affected,
        len(outcomes),
        len({row.workspace_id for row in outcomes}),
    )
    return affected
