"""The `part-rename` job — bring existing names up to the convention.

`domain/parts/naming.py` decides what a part should be called; this
sweeps a catalogue that predates it. Prod is 324 parts of which 127 are
named by the provider's marketing copy and 164 by hand-typed project
prose, so this is a bulk rewrite of the field users read most, on a
deployment with no staging copy behind it. Three things follow from
that:

* **A dry run is the default and writes nothing.** `--apply` is an
  explicit second decision, taken after reading the CSV the dry run
  produced. `run_job` commits whatever the job leaves in the session, so
  "writes nothing" here means "mutates nothing", not "rolls back".
* **Nothing a rename overwrites is lost.** A role suffix
  (`STM32F103C8T6 - Servo integrator`) keeps its role, and a free-text
  name keeps all of it, in a `manual` `alias` custom field — the one
  place a provider refresh will never reach. A name that merely repeated
  the `description` needs no alias: that column still holds it.
  An `alias` that already exists is never overwritten, archived or not:
  `uq_cf_unique` carries no `archived_at` predicate, so an archived row
  still owns the key.
* **The audit row carries counts, never names.** Part names are user
  data and this is a bulk event; the detail belongs in the operator's
  report file, which does not live in the database.

**Most parts will only reach their MPN, not a canonical name.** A
template renders from canonical spec keys and prod's spec rows still
carry the vendor's own key names, so `canonical_name` returns None until
the spec-normalisation backfill runs. Those parts are counted under
`template_unrenderable` and named by their MPN, which is the convention
for them anyway; re-running after the backfill renames them the rest of
the way. That is why a second run is a no-op rather than an error — this
job is expected to be run more than once.

Safe to re-run for a second reason too: `parts.name` has no unique
index, so two parts arriving at the same canonical name is a catalogue
duplicate to resolve, not a failed job. Every consumer keys on the part
id, KiCad included.
"""
from __future__ import annotations

import csv
import logging
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from typing import Any, TextIO
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.audit.service import log as audit_log
from app.domain.custom_fields.models import CustomField
from app.domain.parts.models import Part
from app.domain.parts.naming import (
    ALIAS_CUSTOM_FIELD_KEY,
    CanonicalResult,
    RenameProposal,
    canonical_names,
    propose_rename,
)
from app.domain.workspaces.models import Workspace

logger = logging.getLogger(__name__)

__all__ = [
    "REPORT_COLUMNS",
    "RenameCounts",
    "RenameOutcome",
    "UnknownWorkspaceError",
    "rename_parts",
]


class UnknownWorkspaceError(LookupError):
    """`--workspace` named a workspace that does not exist.

    A `LookupError` because that is the shape `cli/run_job.py::main`
    turns into a usage error and exit 2 for every operator-run job. The
    twin in `services/spec_normalize.py` says the same thing; if a third
    job needs it, hoist both rather than adding another.
    """


# One row per proposed rename. `alias_written` is the text parked in the
# `alias` custom field — empty when there was nothing to preserve or an
# alias was already there.
REPORT_COLUMNS = (
    "workspace_id",
    "part_id",
    "mpn",
    "old_name",
    "new_name",
    "class",
    "alias_written",
    # Where the old name survives the rename — `alias`, `mpn`,
    # `description` or `none`. `alias_written` alone could not tell
    # "there was nothing to preserve" from "text was dropped", which is
    # the only question that matters when reading this file.
    "old_name_preserved_in",
    # Empty when the row was renamed. Otherwise why it was not:
    # `free_excluded` (no `--include-free`), `alias_conflict` (the part
    # already has an `alias` and the rename would have destroyed text),
    # `name_too_long`.
    "skip_reason",
)

# Columns whose text is the recovery copy of something the rename
# overwrites. They are never prefixed with the spreadsheet
# formula-neutralising apostrophe, because a corrupted recovery value
# defeats the point of recording it; `csv.writer` quotes them per
# RFC 4180 and the runbook says to read this file as text.
_VERBATIM_COLUMNS = frozenset({"old_name", "alias_written"})

# First characters Excel and LibreOffice read as the start of a formula.
# Part names are user-supplied free text and this report is written to be
# opened in a spreadsheet, so a cell starting with one of these is
# prefixed with an apostrophe. Same set and same mitigation as the
# frontend's CSV export (`web/src/components/DataTable.tsx`), kept
# identical on purpose — one rule, two exporters.
_FORMULA_LEADERS = frozenset("=+-@\t\r")

# How many parts are classified per `custom_fields` query. Prod's largest
# workspace is a few hundred parts, so this is about bounding the `IN`
# list for a catalogue that grows, not about the catalogue we have.
_BATCH_SIZE = 500

# Why a part with a name to move away from was not renamed.
_SKIP_FREE = "free_excluded"
_SKIP_ALIAS_CONFLICT = "alias_conflict"


@dataclass(frozen=True)
class RenameCounts:
    """What one run saw, in the shape the audit row records."""

    considered: int = 0
    renamed: int = 0
    alias_written: int = 0
    template_unrenderable: int = 0
    skipped_free: int = 0
    skipped_alias_conflict: int = 0

    def __add__(self, other: RenameCounts) -> RenameCounts:
        return RenameCounts(
            considered=self.considered + other.considered,
            renamed=self.renamed + other.renamed,
            alias_written=self.alias_written + other.alias_written,
            template_unrenderable=(
                self.template_unrenderable + other.template_unrenderable
            ),
            skipped_free=self.skipped_free + other.skipped_free,
            skipped_alias_conflict=(
                self.skipped_alias_conflict + other.skipped_alias_conflict
            ),
        )

    def as_comment(self) -> str:
        """The audit comment. Counts only — see the module docstring."""
        return (
            f"considered={self.considered} renamed={self.renamed} "
            f"alias_written={self.alias_written} "
            f"template_unrenderable={self.template_unrenderable} "
            f"skipped_free={self.skipped_free} "
            f"skipped_alias_conflict={self.skipped_alias_conflict}"
        )


@dataclass(frozen=True)
class RenameOutcome:
    counts: RenameCounts
    applied: bool


class RenameReport:
    """Streaming CSV writer over a stream the CALLER owns.

    `stream` is what `run_job_options.report_stream` yields: the file
    `--report` named, or None for stdout. Opening, closing, the
    directory mode and the readable error when the path cannot be
    written all belong to that context manager — every operator-run job
    gets its report on the same terms.

    Streaming rather than buffered, and that is what makes an `--apply`
    run that raises part-way still useful: the rows decided before the
    failure are already on disk. The transaction rolls back; the
    operator keeps the list of what the job was doing when it stopped.
    """

    def __init__(self, stream: TextIO | None) -> None:
        self._handle: TextIO = stream if stream is not None else sys.stdout
        # `csv.writer` returns a private `_csv.writer` with no public
        # name to annotate against.
        self._writer: Any = csv.writer(self._handle)
        self._writer.writerow(REPORT_COLUMNS)

    def write(self, row: Sequence[str]) -> None:
        self._writer.writerow(
            [_neutralise(cell, column) for cell, column in zip(row, REPORT_COLUMNS)]
        )


def rename_parts(
    db: Session,
    *,
    apply: bool = False,
    include_free: bool = False,
    workspace_id: UUID | None = None,
    stream: TextIO | None = None,
) -> RenameOutcome:
    """Classify every active part, report the renames, optionally do them.

    `stream` is where the review CSV goes; the caller opens and closes it
    (`run_job_options.report_stream`). `run_job` also owns the transaction, and
    ends a dry run in ROLLBACK, so a run without `--apply` cannot write
    even if this function were wrong about which mode it is in.
    """
    report = RenameReport(stream)
    counts = _sweep(
        db,
        apply=apply,
        include_free=include_free,
        workspace_id=workspace_id,
        report=report,
    )
    logger.info(
        "job=part-rename mode=%s %s",
        "apply" if apply else "dry-run",
        counts.as_comment(),
    )
    return RenameOutcome(counts=counts, applied=apply)


def _sweep(
    db: Session,
    *,
    apply: bool,
    include_free: bool,
    workspace_id: UUID | None,
    report: RenameReport,
) -> RenameCounts:
    """The sweep itself, writing each decided row as it goes."""
    counts = RenameCounts()
    for workspace in _workspaces(db, workspace_id=workspace_id):
        workspace_counts = RenameCounts()
        for parts in _part_batches(db, workspace_id=workspace.id):
            batch_counts = _process(
                db,
                workspace_id=workspace.id,
                parts=parts,
                apply=apply,
                include_free=include_free,
                report=report,
            )
            workspace_counts += batch_counts
            if apply:
                # Flush here, where the batch that caused a constraint
                # failure is still the batch on screen. Left to
                # autoflush, the error surfaces on the NEXT batch's
                # SELECT, pointing at the wrong parts.
                db.flush()
        counts += workspace_counts
        if apply and workspace_counts.renamed:
            audit_log(
                db,
                ws=workspace,
                user=None,
                action="part.bulk_renamed",
                target_type="part",
                target_ids=None,
                comment=workspace_counts.as_comment(),
            )
    return counts


def _workspaces(db: Session, *, workspace_id: UUID | None) -> Sequence[Workspace]:
    """Every workspace, or the one asked for.

    An id that names no workspace is a usage error, not an empty report:
    an operator converting one workspace at a time needs a typo to say
    so, and the other operator-run jobs answer the same way. `workspaces`
    has no archive flag — a workspace exists or it does not.
    """
    stmt = select(Workspace)
    if workspace_id is not None:
        stmt = stmt.where(Workspace.id == workspace_id)
    workspaces = db.execute(stmt.order_by(Workspace.created_at)).scalars().all()
    if workspace_id is not None and not workspaces:
        raise UnknownWorkspaceError(f"no workspace with id {workspace_id}")
    return workspaces


def _part_batches(db: Session, *, workspace_id: UUID) -> Iterator[Sequence[Part]]:
    """Active parts of one workspace, `_BATCH_SIZE` at a time.

    Keyset pagination on `id`, not `OFFSET`: the run commits once at the
    end but reads at READ COMMITTED, so a part created between two
    batches would shift every later offset and push one part out of the
    sweep. `id` is immutable and the rename does not touch it, so
    "everything after the last one I saw" cannot skip or repeat a row.
    Ordering by it also makes a run reproducible and its report diffable
    against the previous one.
    """
    after: UUID | None = None
    while True:
        stmt = (
            select(Part)
            .where(Part.workspace_id == workspace_id)
            .where(Part.archived_at.is_(None))
            .order_by(Part.id)
            .limit(_BATCH_SIZE)
        )
        if after is not None:
            stmt = stmt.where(Part.id > after)
        parts = db.execute(stmt).scalars().all()
        if not parts:
            return
        yield parts
        if len(parts) < _BATCH_SIZE:
            return
        after = parts[-1].id


def _process(
    db: Session,
    *,
    workspace_id: UUID,
    parts: Sequence[Part],
    apply: bool,
    include_free: bool,
    report: RenameReport,
) -> RenameCounts:
    results = canonical_names(db, workspace_id=workspace_id, parts=parts)
    existing_aliases = _parts_with_an_alias(
        db, workspace_id=workspace_id, part_ids=[part.id for part in parts]
    )
    renamed = aliases = unrenderable = skipped_free = alias_conflicts = 0

    for part in parts:
        # `.get`, because a part outside this workspace is dropped by
        # `canonical_names` rather than answered.
        result = results.get(part.id, CanonicalResult())
        unrenderable += int(result.unrenderable)
        proposal = propose_rename(part, result.name)
        if proposal.new_name is None:
            if proposal.skip_reason:
                report.write(_report_row(workspace_id, part, proposal, alias=None))
            continue

        proposal = _resolve_conflicts(
            proposal, has_alias=part.id in existing_aliases, include_free=include_free
        )
        if proposal.new_name is None:
            skipped_free += int(proposal.skip_reason == _SKIP_FREE)
            alias_conflicts += int(proposal.skip_reason == _SKIP_ALIAS_CONFLICT)
            report.write(_report_row(workspace_id, part, proposal, alias=None))
            continue

        alias = proposal.alias if proposal.preserved_in == "alias" else None
        alias_conflicts += int(proposal.skip_reason == _SKIP_ALIAS_CONFLICT)
        renamed += 1
        aliases += int(bool(alias))
        report.write(_report_row(workspace_id, part, proposal, alias=alias))
        if apply:
            _apply(
                db,
                workspace_id=workspace_id,
                part=part,
                new_name=proposal.new_name,
                alias=alias,
            )

    return RenameCounts(
        considered=len(parts),
        renamed=renamed,
        alias_written=aliases,
        template_unrenderable=unrenderable,
        skipped_free=skipped_free,
        skipped_alias_conflict=alias_conflicts,
    )


def _resolve_conflicts(
    proposal: RenameProposal, *, has_alias: bool, include_free: bool
) -> RenameProposal:
    """Downgrade a proposal against what the part already carries.

    Two rules, both about not destroying text:

    * **A `free` name is left alone unless asked for.** It is the one
      class where the name is somebody's deliberate choice rather than
      an artefact of how the part was imported, and renaming it is a
      judgement call an operator makes with `--include-free`, having
      read the rows this still reports.
    * **A part that already has an `alias` cannot be given another** —
      `uq_cf_unique` allows one per key — so a rename that needed the
      slot would drop its text on the floor. It is skipped instead,
      unless the old name is still readable in `description`, which is
      a weaker guarantee than an alias but not nothing.
    """
    if proposal.classification == "free" and not include_free:
        return replace(proposal, new_name=None, alias=None, skip_reason=_SKIP_FREE)
    if proposal.preserved_in != "alias" or not has_alias:
        return proposal
    if proposal.classification == "description":
        # The column still holds it. Renamed, and the report says where
        # the old name went so a later provider refresh is a known risk
        # rather than a surprise.
        return replace(
            proposal,
            alias=None,
            preserved_in="description",
            skip_reason=_SKIP_ALIAS_CONFLICT,
        )
    return replace(
        proposal,
        new_name=None,
        alias=None,
        preserved_in="none",
        skip_reason=_SKIP_ALIAS_CONFLICT,
    )


def _report_row(
    workspace_id: UUID,
    part: Part,
    proposal: RenameProposal,
    *,
    alias: str | None,
) -> list[str]:
    """One CSV row, in `REPORT_COLUMNS` order."""
    return [
        str(workspace_id),
        str(part.id),
        part.mpn or "",
        part.name or "",
        proposal.new_name or "",
        proposal.classification,
        alias or "",
        proposal.preserved_in,
        proposal.skip_reason,
    ]


def _apply(
    db: Session,
    *,
    workspace_id: UUID,
    part: Part,
    new_name: str,
    alias: str | None,
) -> None:
    if alias:
        # Written before the rename so a failure here cannot leave the
        # old text nowhere: the whole run shares one transaction.
        db.add(
            CustomField(
                workspace_id=workspace_id,
                object_type="part",
                object_id=part.id,
                key=ALIAS_CUSTOM_FIELD_KEY,
                value=alias,
                source="manual",
            )
        )
    part.name = new_name
    # `updated_at` is the mixin's `onupdate`. `updated_by` is cleared
    # rather than left alone: leaving it would credit this rename to
    # whoever last edited the part, and there is no system actor to name
    # instead. The audit row is where "a job did this" is recorded.
    part.updated_by = None


def _parts_with_an_alias(
    db: Session, *, workspace_id: UUID, part_ids: Sequence[UUID]
) -> set[UUID]:
    """Which of these parts already own the `alias` key.

    Archived rows included on purpose — `uq_cf_unique` has no
    `archived_at` predicate, so an archived row still holds the key and a
    second insert is an `IntegrityError`, not an overwrite.
    """
    if not part_ids:
        return set()
    rows = db.execute(
        select(CustomField.object_id)
        .where(CustomField.workspace_id == workspace_id)
        .where(CustomField.object_type == "part")
        .where(CustomField.object_id.in_(part_ids))
        .where(CustomField.key == ALIAS_CUSTOM_FIELD_KEY)
    ).all()
    return {row.object_id for row in rows}


def _neutralise(cell: str, column: str) -> str:
    """Stop a spreadsheet reading a part name as a formula.

    Skipped for `_VERBATIM_COLUMNS`: those hold the recovery copy of
    text the rename overwrites, and an apostrophe glued to the front of
    it is a corrupted recovery value. `csv.writer` still quotes them per
    RFC 4180, and the runbook says to read the file as text.
    """
    if column in _VERBATIM_COLUMNS:
        return cell
    return f"'{cell}" if cell[:1] in _FORMULA_LEADERS else cell
