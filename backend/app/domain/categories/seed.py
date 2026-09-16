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
from collections import Counter
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
    EMPTY_IS_SET_FIELDS,
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
    "REASON_ARCHIVED",
    "REASON_NAME_AMBIGUOUS",
    "REASON_NAME_TAKEN",
    "REASON_PARENT_MISSING",
    "REASON_RACE",
    "REASON_SLUG_TAKEN",
    "REASON_TOO_DEEP",
    "SKIPPED",
    "UNCHANGED",
    "UPDATED",
    "SeedOutcome",
    "assert_workspace_exists",
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

# The two partial unique indexes a lost race can trip. COPIES of
# `categories/service.py`'s constants rather than an import, so a CLI job
# does not drag the FastAPI-facing service module (and its `fastapi`
# import chain) into its own; `tests/test_category_seed.py::
# test_the_constraint_names_match_the_service` fails if they drift. Same
# deliberate-copy pattern as `PLACEHOLDER_PATTERN` in
# `categories/schemas.py`.
_UQ_WS_NAME = "uq_part_categories_ws_name"
_UQ_WS_SLUG = "uq_part_categories_ws_slug"
_RACEABLE_CONSTRAINTS: frozenset[str] = frozenset({_UQ_WS_NAME, _UQ_WS_SLUG})
REASON_RACE = "another writer took a name or slug mid-run; re-run the job"
REASON_NAME_AMBIGUOUS = (
    "two active categories share this name; rename one so the seed knows "
    "which it means"
)


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
    of them for truthiness. The exceptions are listed in
    `seed_tables.EMPTY_IS_SET_FIELDS`, where an empty container is a
    real instruction rather than an absent value.
    """
    if value is None:
        return True
    if field in EMPTY_IS_SET_FIELDS:
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
        # `uq_part_categories_ws_name` is case-SENSITIVE, so "Resistors"
        # and "resistors" are two legal rows that collapse to one key in
        # the maps above. Counting them is how `_plan_one` can refuse to
        # pick one arbitrarily — the caller gets a rename to do, not a
        # coin toss that a re-run could decide differently.
        self._name_counts: Counter[str] = Counter(r.name.lower() for r in active)
        self._parent_name_counts: Counter[tuple[UUID | None, str]] = Counter(
            (r.parent_id, r.name.lower()) for r in active
        )
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
        self._name_counts[row.name.lower()] += 1
        self._parent_name_counts[(row.parent_id, row.name.lower())] += 1

    def is_ambiguous(self, seed: SeedCategory, parent_id: UUID | None) -> bool:
        """Whether more than one active row answers this seed row's lookup.

        Scoped exactly like `match`: workspace-wide for a root, under the
        resolved parent for a child.
        """
        if seed.parent is None:
            return self._name_counts[seed.name.lower()] > 1
        return self._parent_name_counts[(parent_id, seed.name.lower())] > 1

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
    """One workspace's categories, oldest first.

    The ORDER BY is not cosmetic. The maps below are keyed on a
    lower-cased name while the unique index is case-sensitive, so two
    rows can share a key; without a stable order the survivor would be
    whatever Postgres returned first, and two runs could disagree about
    which category they filled. Oldest-first also makes the survivor the
    one the user created first, which is the better guess where
    `is_ambiguous` does not already refuse to guess at all.
    """
    rows = list(
        db.execute(
            select(PartCategory)
            .where(PartCategory.workspace_id == workspace_id)
            .order_by(PartCategory.created_at.asc(), PartCategory.id.asc())
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

    On `--apply`, takes the workspace-tree advisory lock the reparent
    path takes, before the index is read: every decision below rests on
    that snapshot, and a concurrent create could otherwise take the name
    this run is about to use.

    A dry run does NOT take it. The lock is exclusive per workspace and
    the report can be minutes of work across an estate; blocking every
    category write in every workspace to produce a file that changes
    nothing is a cost with no matching benefit. The plan may be a moment
    stale, which is what a plan is.
    """
    if apply:
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
        # Keyed by the parent's NAME because `SeedCategory.parent` holds
        # one; `test_seed_names_are_unique` is what makes that key
        # unambiguous, and the same test is why a path key would buy
        # nothing here.
        parent_id = resolved.get(seed.parent)
        if parent_id is None:
            return outcome(SKIPPED, detail=REASON_PARENT_MISSING), None

    if index.is_ambiguous(seed, parent_id):
        return outcome(SKIPPED, detail=REASON_NAME_AMBIGUOUS), None

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
    # The minted id is real only under `--apply`. A dry run needs one so
    # children can be planned against it, but printing it would put a
    # UUID that will never exist into the operator's report.
    return outcome(CREATED, category_id=row.id if apply else None), row


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


def _workspaces(db: Session, *, workspace_id: UUID | None) -> list[Workspace]:
    assert_workspace_exists(db, workspace_id)
    stmt = select(Workspace).order_by(Workspace.name.asc(), Workspace.id.asc())
    if workspace_id is not None:
        stmt = stmt.where(Workspace.id == workspace_id)
    return list(db.execute(stmt).scalars())


def _summary(outcomes: Iterable[SeedOutcome]) -> str:
    counts = Counter(outcome.action for outcome in outcomes)
    return " ".join(f"{action}={counts[action]}" for action in sorted(counts))


def _write_audit(db: Session, *, ws: Workspace, outcomes: Sequence[SeedOutcome]) -> None:
    """One `audit_log` row for one changed workspace.

    `target_ids` carries every row this run WROTE, created and updated
    alike — an update writes KiCad metadata onto a category a user made,
    which is exactly the kind of change the audit trail exists to make
    traceable (CLAUDE.md: stable target ids when available). A workspace
    where nothing changed gets no row at all: "I looked and did nothing"
    is noise in a table on-call reads. The comment stays action counts
    only — a category name is user data.
    """
    written = [o.category_id for o in outcomes if o.is_write and o.category_id]
    if not written:
        return
    audit_log(
        db,
        ws=ws,
        user=None,
        action=AUDIT_ACTION,
        target_type="part_category",
        target_ids=written,
        comment=_summary(outcomes),
    )


def _violated_constraint(exc: IntegrityError) -> str | None:
    """The constraint name Postgres reported, if it reported one."""
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diag, "constraint_name", None)


def _seed_one_workspace(
    db: Session,
    *,
    ws: Workspace,
    apply: bool,
    seeds: Sequence[SeedCategory],
) -> list[SeedOutcome]:
    """One workspace's seed, contained so a lost race can't take the run
    down with it.

    The uniqueness pre-checks read a snapshot, and the workspace-tree
    advisory lock does not cover every writer that could invalidate it:
    `categories/service.py::create_category` only takes the lock when
    the new category has a parent, so a concurrent create of a *root*
    can take a name between this run's check and its flush. That is a
    lost race, not a bug, and losing it must cost one workspace rather
    than the twenty after it — hence the savepoint. The operator
    re-runs; the job is idempotent, so the second run picks up whatever
    the first did not get.

    Only the two uniqueness constraints are treated that way. A
    workspace-FK trigger violation, a NOT NULL, a CHECK or a failure
    writing the audit row is a bug in this job or a corrupt tree, and
    telling the operator to "re-run" would hide it behind an
    instruction that can never work — so anything else is re-raised.
    """
    if not apply:
        return plan_workspace(db, ws=ws, apply=False, seeds=seeds)
    outcomes: list[SeedOutcome] = []
    try:
        with db.begin_nested():
            outcomes = plan_workspace(db, ws=ws, apply=True, seeds=seeds)
            _write_audit(db, ws=ws, outcomes=outcomes)
    except IntegrityError as exc:
        constraint = _violated_constraint(exc)
        if constraint not in _RACEABLE_CONSTRAINTS:
            raise
        # No `exc_info`: a SQLAlchemy traceback carries the statement's
        # bound parameters, which here are the workspace's category names
        # and descriptions. The constraint name is the whole diagnosis.
        logger.warning(
            "category-seed workspace=%s status=raced constraint=%s",
            ws.id,
            constraint,
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
