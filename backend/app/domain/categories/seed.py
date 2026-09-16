"""The `category-seed` job: give every workspace the passive category
tree the KiCad Value templates and `Device:*` symbols hang off.

Idempotent, additive, and deliberately timid. It creates categories a
workspace is missing and fills in KiCad metadata that was never set, and
it does **nothing** else:

* **It never renames, re-parents or re-orders a category.** A workspace
  that calls its bucket "Elcos" instead of "Electrolytic" keeps it: the
  seed matches by name (case-insensitively) under the expected parent,
  and a name it does not recognise is a category it does not know about.
  Where a name it wants is already used elsewhere in the workspace, it
  reports the collision instead of resolving it.
* **It never overwrites a value a user set.** A field is filled only
  when it is NULL — or blank, or an empty array where nothing
  downstream can tell that from NULL. `kicad_fields = []` is the one
  empty container that counts as *set*, because an explicit empty list
  is how a category says "emit no symbol fields" against a parent that
  emits some (see `domain/eda/kicad_specs.py`).
* **It never deletes anything.**

A dry run (the default) plans the whole thing and writes nothing. It is
the same code path `--apply` takes, minus the `setattr` and the
`db.add` — which is why `apply` is threaded down to `_plan_one` rather
than checked once at the top: a plan that mutated a *persistent*
category and relied on a later rollback would still be one autoflush
away from writing. `run_job` rolls the transaction back after a dry run
as well, so a bug here has to get past two independent guards.

Workspace isolation: every read filters on `workspace_id`, a created
row's parent always comes from the same workspace's index, and
`--workspace` narrows to one. Nothing here reads across workspaces, so
the report for workspace A cannot name a row in B.

The seed data itself — which categories, which `Device:*` symbol, which
`value_template` — lives in `seed_tables.py`. See ADR-0034 and plan
items A6/B3.
"""
from __future__ import annotations

import csv
import logging
import sys
import uuid
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, TextIO
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.audit.service import log as audit_log
from app.domain.categories.models import PartCategory
from app.domain.categories.seed_tables import (
    SEED_CATEGORIES,
    SEEDABLE_FIELDS,
    SeedCategory,
)
from app.domain.categories.tree import MAX_DEPTH, depth_of, lock_workspace_tree
from app.domain.workspaces.models import Workspace

logger = logging.getLogger(__name__)

__all__ = [
    "AUDIT_ACTION",
    "CREATED",
    "CSV_COLUMNS",
    "REASON_RACE",
    "SKIPPED",
    "UNCHANGED",
    "UPDATED",
    "SeedOutcome",
    "iter_seed_paths",
    "plan_workspace",
    "run_category_seed",
    "seed_all_workspaces",
    "write_report",
]

AUDIT_ACTION = "category.seed"

CSV_COLUMNS = (
    "workspace_id",
    "workspace_name",
    "path",
    "action",
    "category_id",
    "detail",
)

# What `SeedOutcome.action` can be.
CREATED = "created"
UPDATED = "updated"
UNCHANGED = "unchanged"
SKIPPED = "skipped"

# `SeedOutcome.detail` reasons for `SKIPPED`.
REASON_NAME_TAKEN = "name used by another category"
REASON_SLUG_TAKEN = "library slug used by another category"
REASON_ARCHIVED = "an archived category already has this name here"
REASON_PARENT_MISSING = "parent category could not be resolved"
REASON_TOO_DEEP = f"would exceed the {MAX_DEPTH}-level nesting cap"
REASON_RACE = "another writer took a name or slug mid-run; re-run the job"


@dataclass(frozen=True)
class SeedOutcome:
    """One seed row's verdict in one workspace — a CSV line.

    `category_id` is the row the seed row resolved to: the existing
    category it matched, or the id a create used (a dry run mints the
    same kind of id so children can be planned against it, and then
    throws it away). ``None`` on a skip.
    """

    workspace_id: UUID
    workspace_name: str
    path: str
    action: str
    category_id: UUID | None = None
    detail: str = ""

    @property
    def is_write(self) -> bool:
        return self.action in (CREATED, UPDATED)

    def as_row(self) -> dict[str, str]:
        return {
            "workspace_id": str(self.workspace_id),
            "workspace_name": self.workspace_name,
            "path": self.path,
            "action": self.action,
            "category_id": str(self.category_id) if self.category_id else "",
            "detail": self.detail,
        }


def _is_unset(field: str, value: object) -> bool:
    """Whether the seed may fill `field`, given the row's current value.

    Blank strings and empty arrays count as unset because nothing
    downstream can tell them from NULL — `kicad_library.py` tests each
    of them for truthiness. `kicad_fields` is the exception: an empty
    list there is a real instruction that stops the inheritance walk in
    `kicad_specs.py`, so only NULL is unset.
    """
    if value is None:
        return True
    if field == "kicad_fields":
        return False
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not value
    return False


def _seed_value(field: str, seed: SeedCategory) -> Any:
    """The seed's value for `field`, in the shape the column wants.

    The tables use tuples so a seed row stays hashable and immutable;
    Postgres `ARRAY` and `JSONB` both want a list.
    """
    value = getattr(seed, field)
    return list(value) if isinstance(value, tuple) else value


class _WorkspaceIndex:
    """One workspace's categories, indexed the four ways the seed asks.

    Built from a single workspace-scoped query. Rows the seed plans are
    folded back in as they are planned, so a child resolves against a
    parent planned earlier in the same run — in a dry run too, where
    that parent exists only as a provisional id.
    """

    def __init__(self, rows: Sequence[PartCategory]) -> None:
        self.parent_map: dict[UUID, UUID | None] = {r.id: r.parent_id for r in rows}
        active = [r for r in rows if r.archived_at is None]
        self.by_name: dict[str, PartCategory] = {r.name.lower(): r for r in active}
        self.by_slug: dict[str, PartCategory] = {r.library_slug: r for r in active}
        self.by_parent_name: dict[tuple[UUID | None, str], PartCategory] = {
            (r.parent_id, r.name.lower()): r for r in active
        }
        archived = [r for r in rows if r.archived_at is not None]
        self.archived_by_name: dict[str, PartCategory] = {
            r.name.lower(): r for r in archived
        }
        self.archived_by_parent_name: dict[tuple[UUID | None, str], PartCategory] = {
            (r.parent_id, r.name.lower()): r for r in archived
        }

    def add(self, row: PartCategory) -> None:
        self.parent_map[row.id] = row.parent_id
        self.by_name[row.name.lower()] = row
        self.by_slug[row.library_slug] = row
        self.by_parent_name[(row.parent_id, row.name.lower())] = row

    def match(self, seed: SeedCategory, parent_id: UUID | None) -> PartCategory | None:
        """The active row this seed row already has, if any.

        A root matches by name anywhere in the workspace rather than at
        the root only: a workspace that filed *Capacitors* under its own
        *Passives* umbrella owns that bucket, and the seed's job is to
        hang the missing leaves off it — not to build a second
        *Capacitors* at the top level.
        """
        if seed.parent is None:
            return self.by_name.get(seed.name.lower())
        return self.by_parent_name.get((parent_id, seed.name.lower()))

    def archived_match(
        self, seed: SeedCategory, parent_id: UUID | None
    ) -> PartCategory | None:
        """The same lookup over archived rows, so a retired category is
        left retired wherever the user had filed it.

        Scoped exactly like `match`, or a root the user archived under
        their own umbrella would be silently re-created at the top
        level — legal (the unique indexes cover active rows only) and
        the opposite of what archiving it meant.
        """
        if seed.parent is None:
            return self.archived_by_name.get(seed.name.lower())
        return self.archived_by_parent_name.get((parent_id, seed.name.lower()))


def _load_index(db: Session, *, workspace_id: UUID) -> _WorkspaceIndex:
    rows = list(
        db.execute(
            select(PartCategory).where(PartCategory.workspace_id == workspace_id)
        ).scalars()
    )
    return _WorkspaceIndex(rows)


def _path_of(seed: SeedCategory) -> str:
    return f"{seed.parent} / {seed.name}" if seed.parent else seed.name


def _fillable_fields(existing: PartCategory, seed: SeedCategory) -> list[str]:
    """The fields the seed has a value for that `existing` left unset, in
    `SEEDABLE_FIELDS` order. Reads only — the caller decides whether to
    write them, which is what makes the dry run a dry run."""
    return [
        field
        for field in SEEDABLE_FIELDS
        if _seed_value(field, seed) is not None
        and _is_unset(field, getattr(existing, field))
    ]


def _new_row(
    seed: SeedCategory, *, workspace_id: UUID, parent_id: UUID | None
) -> PartCategory:
    """An unattached `PartCategory` carrying the whole seed row.

    The id is assigned here rather than by the column default so a dry
    run can plan children against a parent it is not going to create.
    """
    row = PartCategory(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name=seed.name,
        sort_order=seed.sort_order,
        library_slug=seed.library_slug,
        parent_id=parent_id,
    )
    for field in SEEDABLE_FIELDS:
        setattr(row, field, _seed_value(field, seed))
    return row


def plan_workspace(
    db: Session,
    *,
    ws: Workspace,
    apply: bool,
    seeds: Sequence[SeedCategory] = SEED_CATEGORIES,
) -> list[SeedOutcome]:
    """Plan — and, when `apply`, perform — the seed for one workspace.

    Takes the workspace-tree advisory lock the reparent path takes,
    before the index is read: every decision below rests on that
    snapshot, and a concurrent create could otherwise take the name this
    run is about to use.
    """
    lock_workspace_tree(db, workspace_id=ws.id)
    index = _load_index(db, workspace_id=ws.id)
    resolved: dict[str, UUID] = {}
    outcomes: list[SeedOutcome] = []

    for seed in seeds:
        outcome, row = _plan_one(
            seed, ws=ws, index=index, resolved=resolved, apply=apply
        )
        outcomes.append(outcome)
        if row is None:
            continue
        resolved[seed.name] = row.id
        if outcome.action == CREATED:
            index.add(row)
            if apply:
                db.add(row)

    if apply:
        db.flush()
    return outcomes


def _plan_one(
    seed: SeedCategory,
    *,
    ws: Workspace,
    index: _WorkspaceIndex,
    resolved: dict[str, UUID],
    apply: bool,
) -> tuple[SeedOutcome, PartCategory | None]:
    """One seed row's verdict, plus the row it resolved to.

    The second element is the category later rows should treat as this
    seed row's parent — the existing one, or the one that would be
    created. ``None`` on a skip, which is what makes a skipped parent
    skip its children too.
    """
    path = _path_of(seed)

    def outcome(
        action: str, *, category_id: UUID | None = None, detail: str = ""
    ) -> SeedOutcome:
        return SeedOutcome(ws.id, ws.name, path, action, category_id, detail)

    parent_id: UUID | None = None
    if seed.parent is not None:
        parent_id = resolved.get(seed.parent)
        if parent_id is None:
            return outcome(SKIPPED, detail=REASON_PARENT_MISSING), None

    existing = index.match(seed, parent_id)
    if existing is not None:
        fields = _fillable_fields(existing, seed)
        if apply:
            for field in fields:
                setattr(existing, field, _seed_value(field, seed))
        action = UPDATED if fields else UNCHANGED
        return (
            outcome(action, category_id=existing.id, detail=", ".join(fields)),
            existing,
        )

    if index.archived_match(seed, parent_id) is not None:
        return outcome(SKIPPED, detail=REASON_ARCHIVED), None
    name_clash = index.by_name.get(seed.name.lower())
    if name_clash is not None:
        return (
            outcome(SKIPPED, detail=f"{REASON_NAME_TAKEN}: {name_clash.library_slug}"),
            None,
        )
    slug_clash = index.by_slug.get(seed.library_slug)
    if slug_clash is not None:
        return (
            outcome(SKIPPED, detail=f"{REASON_SLUG_TAKEN}: {slug_clash.name}"),
            None,
        )
    if parent_id is not None and depth_of(index.parent_map, parent_id) >= MAX_DEPTH:
        return outcome(SKIPPED, detail=REASON_TOO_DEEP), None

    row = _new_row(seed, workspace_id=ws.id, parent_id=parent_id)
    return outcome(CREATED, category_id=row.id), row


def _workspaces(db: Session, *, workspace_id: UUID | None) -> list[Workspace]:
    stmt = select(Workspace).order_by(Workspace.name.asc(), Workspace.id.asc())
    if workspace_id is not None:
        stmt = stmt.where(Workspace.id == workspace_id)
    return list(db.execute(stmt).scalars())


def _summary(outcomes: Iterable[SeedOutcome]) -> str:
    counts: dict[str, int] = {}
    for outcome in outcomes:
        counts[outcome.action] = counts.get(outcome.action, 0) + 1
    return " ".join(f"{action}={counts[action]}" for action in sorted(counts))


def _write_audit(db: Session, *, ws: Workspace, outcomes: Sequence[SeedOutcome]) -> None:
    """One `audit_log` row for one changed workspace.

    A workspace where nothing changed gets none: a row saying "I looked
    and did nothing" is noise in a table on-call reads. The comment is
    action counts only — never a category name, which is user data.
    """
    if not any(o.is_write for o in outcomes):
        return
    audit_log(
        db,
        ws=ws,
        user=None,
        action=AUDIT_ACTION,
        target_type="part_category",
        target_ids=[
            o.category_id
            for o in outcomes
            if o.action == CREATED and o.category_id is not None
        ]
        or None,
        comment=_summary(outcomes),
    )


def _seed_one_workspace(
    db: Session,
    *,
    ws: Workspace,
    apply: bool,
    seeds: Sequence[SeedCategory],
) -> list[SeedOutcome]:
    """One workspace's seed, contained so a collision can't take the run
    down with it.

    The uniqueness pre-checks read a snapshot, and the workspace-tree
    advisory lock does not cover every writer that could invalidate it:
    `categories/service.py::create_category` only takes the lock when
    the new category has a parent, so a concurrent create of a *root*
    can take a name between this run's check and its flush. That is a
    lost race, not a bug, and losing it must cost one workspace rather
    than the twenty after it — hence the savepoint. The operator re-runs;
    the job is idempotent, so the second run picks up whatever the first
    did not get.
    """
    if not apply:
        return plan_workspace(db, ws=ws, apply=False, seeds=seeds)
    try:
        with db.begin_nested():
            outcomes = plan_workspace(db, ws=ws, apply=True, seeds=seeds)
            _write_audit(db, ws=ws, outcomes=outcomes)
    except IntegrityError:
        logger.warning(
            "category-seed workspace=%s status=raced", ws.id, exc_info=True
        )
        return [SeedOutcome(ws.id, ws.name, "(workspace)", SKIPPED, None, REASON_RACE)]
    return outcomes


def seed_all_workspaces(
    db: Session,
    *,
    apply: bool,
    workspace_id: UUID | None = None,
    seeds: Sequence[SeedCategory] = SEED_CATEGORIES,
) -> list[SeedOutcome]:
    """Seed every workspace (or just one), returning the whole report.

    On `--apply`, each workspace that actually changed gets exactly one
    `audit_log` row naming the created categories, written in the same
    transaction as the change (the universal-audit invariant).
    """
    outcomes: list[SeedOutcome] = []
    for ws in _workspaces(db, workspace_id=workspace_id):
        workspace_outcomes = _seed_one_workspace(
            db, ws=ws, apply=apply, seeds=seeds
        )
        outcomes += workspace_outcomes
        logger.info(
            "category-seed workspace=%s apply=%s %s",
            ws.id,
            apply,
            _summary(workspace_outcomes),
        )
    return outcomes


def write_report(outcomes: Iterable[SeedOutcome], stream: TextIO) -> None:
    """The CSV report. Goes to stdout so it stays separable from the
    logging, which `run_job` sends to stderr."""
    writer = csv.DictWriter(stream, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for outcome in outcomes:
        writer.writerow(outcome.as_row())


def run_category_seed(
    db: Session,
    *,
    apply: bool = False,
    workspace_id: UUID | None = None,
    stream: TextIO | None = None,
) -> int:
    """`run_job` entry point. Returns the number of rows written.

    In a dry run that count is what *would* be written — the number the
    operator is deciding about — and nothing reaches the database.
    """
    outcomes = seed_all_workspaces(db, apply=apply, workspace_id=workspace_id)
    write_report(outcomes, stream if stream is not None else sys.stdout)
    return sum(1 for outcome in outcomes if outcome.is_write)


def iter_seed_paths(seeds: Sequence[SeedCategory] = SEED_CATEGORIES) -> Iterator[str]:
    """Every seed row's ` / `-joined path — what the tests resolve
    against `spec_schema.category_slug_for`, and what the docs list."""
    for seed in seeds:
        yield _path_of(seed)
