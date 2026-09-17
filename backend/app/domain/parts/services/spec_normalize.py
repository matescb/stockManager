"""The `spec-normalize` backfill job (A5).

A3 normalises a part's provider specs when that part is next imported or
refreshed. This job does the 9,377 rows prod already carries, in one
pass, without touching a provider API: it re-keys them onto the canonical
schema, retires the customs codes and the ~1,000 `-` placeholders, fills
`custom_fields.provider` and `value_num`, and files the 118 parts that
have no category from their provider's own taxonomy.

Run it dry, read the CSV, take a `pg_dump`, then run it with `--apply`
(`docs/runbooks/spec-normalize.md`). Three properties make that workflow
honest:

* **`--dry-run` is the default and writes nothing.** Every batch runs
  inside a SAVEPOINT that is rolled back, so the plan is produced by the
  same code that would apply it rather than by a second implementation
  that could disagree with it.
* **it is idempotent.** A row already carrying its canonical key, parsed
  value, provider and sidecar is not a change, so a second run reports
  zero.
* **it is resumable.** One commit per batch on apply, and one
  `audit_log` row per workspace. A run killed by a `timeout` keeps
  everything it finished.

The row-level rules — what is archived, what is left to the user, why
`reconcile_provider_specs` is not the writer here — are in
`spec_normalize_rows.py`. ADR-0021 owns the job registry; ADR-0034 owns
the schema.
"""
from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TextIO
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.advisory_locks import SPEC_NORMALIZE_LOCK_CLASSID
from app.domain.audit.service import log_ids as audit_log_ids
from app.domain.categories.service import CategoryIndex, category_index
from app.domain.custom_fields.models import CustomField
from app.domain.parts.models import Part
from app.domain.parts.provider_fields import KNOWN_PROVIDER_NAMES
from app.domain.parts.services.spec_normalize_report import (
    ACTION_CATEGORY,
    ACTIONS,
    REPORT_COLUMNS,
    Change,
    NormalizeReport,
)
from app.domain.parts.services.spec_normalize_rows import normalize_part_rows
from app.domain.parts.services.spec_reconcile import apply_provider_category
from app.domain.workspaces.models import Workspace

logger = logging.getLogger(__name__)

__all__ = [
    "AUDIT_ACTION",
    "DEFAULT_BATCH_SIZE",
    "JOB_NAME",
    "REPORT_COLUMNS",
    "NormalizeOutcome",
    "UnknownWorkspaceError",
    "normalize_specs",
]


class UnknownWorkspaceError(LookupError):
    """`--workspace` named a workspace that does not exist.

    A `LookupError` because that is the shape `cli/run_job.py::main`
    already turns into a usage error and exit 2 for the other
    operator-run jobs.
    """


JOB_NAME = "spec-normalize"
AUDIT_ACTION = "part.specs_normalized"

#: Parts per transaction on apply. Small enough that a killed run loses
#: little, large enough that 324 prod parts are two round trips.
DEFAULT_BATCH_SIZE = 200

#: How many unmapped raw keys the report lists. The list exists to be
#: read and turned into alias-table entries, so it is a shortlist.
UNMAPPED_REPORT_LIMIT = 30

#: Counters that are not actions: they say what the job deliberately did
#: NOT do, which is the half of the summary a reviewer checks.
COUNTER_KEPT_MANUAL = "kept_manual"
COUNTER_KEPT_OTHER = "kept_other_provider"
COUNTER_UNATTRIBUTED = "unattributed"
_EXTRA_COUNTERS = (COUNTER_KEPT_MANUAL, COUNTER_KEPT_OTHER, COUNTER_UNATTRIBUTED)

# Past this many key names an audit comment stops being something a human
# reads in a timeline. Same grammar as `spec_reconcile.py`.
_AUDIT_KEY_LIMIT = 20


@dataclass(frozen=True)
class NormalizeOutcome:
    """What the run did, or would have done."""

    applied: bool
    parts: int
    changes: int
    #: Every action and counter, summed across workspaces. Missing keys
    #: read as 0 — it is a `Counter`.
    counts: Mapping[str, int]
    per_workspace: Mapping[UUID, Mapping[str, int]]
    #: `(category_slug, raw_key, count)`, most frequent first.
    unmapped: tuple[tuple[str, str, int], ...]


def normalize_specs(
    db: Session,
    *,
    apply: bool = False,
    workspace_id: UUID | None = None,
    stream: TextIO | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> NormalizeOutcome:
    """Re-key every workspace's provider specs onto the canonical schema.

    `workspace_id` limits the run to one workspace; omitted, every
    workspace is processed in id order. `stream` is where the review CSV
    goes — the file `--report` named, or ``None`` for stdout. The CLI
    opens and closes it (`run_job._report_stream`), so every operator-run
    job reports on the same terms.

    Caller owns the session. On apply this function commits per batch, so
    it takes a SESSION-level advisory lock rather than relying on
    `run_job`'s transaction-scoped one, which Postgres drops at the first
    COMMIT.
    """
    if not _try_acquire_lock(db):
        logger.info("%s skipped: another run holds the lock", JOB_NAME)
        return NormalizeOutcome(apply, 0, 0, Counter(), {}, ())
    try:
        return _run(
            db,
            apply=apply,
            workspace_id=workspace_id,
            stream=stream,
            batch_size=batch_size,
        )
    except Exception:
        # Ahead of the unlock, not after it: the unlock is a statement, and
        # a statement on a session left in a failed transaction raises
        # `PendingRollbackError` — which would replace whatever actually
        # went wrong with a message about the lock.
        db.rollback()
        raise
    finally:
        _release_lock(db)


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------
def _run(
    db: Session,
    *,
    apply: bool,
    workspace_id: UUID | None,
    stream: TextIO | None,
    batch_size: int,
) -> NormalizeOutcome:
    per_workspace: dict[UUID, Counter[str]] = {}
    unmapped: Counter[tuple[str, str]] = Counter()
    parts = changes = 0

    report = NormalizeReport(stream)
    for ws in _workspaces(db, workspace_id):
        counts: Counter[str] = Counter()
        canonical: set[str] = set()
        part_ids = _part_ids(db, ws_id=ws.id)
        # One tree read for the whole workspace. Filing a part changes
        # `parts.category_id`, never `part_categories`, so the index
        # stays true for every batch — and the rows in it are never
        # modified, so a dry run's savepoint rollback leaves it intact.
        index = category_index(db, ws_id=ws.id)
        done = 0
        for batch in _batches(part_ids, batch_size):
            with _batch_transaction(db, apply=apply):
                changes += _process_batch(
                    db,
                    ws=ws,
                    part_ids=batch,
                    report=report,
                    counts=counts,
                    unmapped=unmapped,
                    canonical=canonical,
                    index=index,
                )
            report.flush()
            done += len(batch)
            # Both numbers are cumulative within the workspace, so the
            # line reads as progress rather than as a batch receipt.
            logger.info(
                "%s workspace=%s parts=%d/%d changes=%d apply=%s",
                JOB_NAME,
                ws.id,
                done,
                len(part_ids),
                sum(counts[action] for action in ACTIONS),
                apply,
            )
        parts += len(part_ids)
        per_workspace[ws.id] = counts
        if apply and any(counts[action] for action in ACTIONS):
            _audit(db, ws_id=ws.id, counts=counts, canonical=canonical)
            db.commit()
    ranked = _rank_unmapped(unmapped)
    report.write_summary(per_workspace=per_workspace, unmapped=ranked)

    totals: Counter[str] = Counter()
    for counts in per_workspace.values():
        totals.update(counts)
    logger.info(
        "%s done apply=%s parts=%d changes=%d %s",
        JOB_NAME,
        apply,
        parts,
        changes,
        " ".join(f"{name}={totals[name]}" for name in (*ACTIONS, *_EXTRA_COUNTERS)),
    )
    return NormalizeOutcome(apply, parts, changes, totals, per_workspace, ranked)


def _process_batch(
    db: Session,
    *,
    ws: Workspace,
    part_ids: Sequence[UUID],
    report: NormalizeReport,
    counts: Counter[str],
    unmapped: Counter[tuple[str, str]],
    canonical: set[str],
    index: CategoryIndex,
) -> int:
    parts = _parts(db, ws_id=ws.id, part_ids=part_ids)
    rows_by_part = _rows_by_part(db, ws_id=ws.id, part_ids=part_ids)
    written = 0
    for part in parts:
        rows = rows_by_part.get(part.id, [])
        provider = _resolve_provider(part, ws)
        slug, filed = _file_part(
            db, ws=ws, part=part, rows=rows, index=index, provider=provider
        )
        outcome = normalize_part_rows(
            part_rows=rows, category_slug=slug, default_provider=provider
        )
        # `normalize_part_rows` takes no session, so the rows it wants
        # INSERTED come back on the outcome. Adding them here puts them
        # inside `_batch_transaction`, which is what makes a dry run
        # discard them along with every rename it planned.
        for new_row in outcome.new_rows:
            db.add(new_row)
        for change in ((filed,) if filed else ()) + outcome.changes:
            report.write(
                workspace_id=ws.id, part_id=part.id, mpn=part.mpn, change=change
            )
            counts[change.action] += 1
            written += 1
        counts[COUNTER_KEPT_MANUAL] += outcome.kept_manual
        counts[COUNTER_KEPT_OTHER] += outcome.kept_other_provider
        counts[COUNTER_UNATTRIBUTED] += 1 if outcome.unattributed else 0
        unmapped.update(outcome.unmapped)
        canonical.update(outcome.canonical)
    return written


def _file_part(
    db: Session,
    *,
    ws: Workspace,
    part: Part,
    rows: Sequence[CustomField],
    index: CategoryIndex,
    provider: str | None,
) -> tuple[str | None, Change | None]:
    """Give the part a category if it has none, and return its schema slug.

    Category first, because the category picks the schema and the schema
    is what tells a resistor's `Temperature Coefficient` (ppm/°C) from a
    ceramic capacitor's (a dielectric name) under the same vendor key —
    the same ordering `provider_import.py` uses.

    Every part goes through `apply_provider_category`, including the ones
    that already have a category. It is the single place that knows the
    slug is not simply "the part's category": a part filed under the
    "Capacitors" root classifies to nothing, because the dielectric is
    the spec set, and so does one the user filed under "Bias network" —
    in both cases the provider's own taxonomy still knows what the part
    is. Deriving the slug from the part's category here instead would
    hand the common schema to most of the parts the backfill exists to
    re-key. It only ever WRITES a NULL `category_id`, so a category the
    user chose is still safe.

    `index` is the workspace's whole category tree, read once per
    workspace and threaded through — without it every part costs a full
    `part_categories` read, which is the N+1 the index exists to avoid.
    """
    previous_editor = part.updated_by
    outcome = apply_provider_category(
        db,
        ws_id=ws.id,
        part=part,
        provider_name=provider or "",
        provider_category=_provider_category_text(rows, provider),
        description=part.description,
        index=index,
    )
    if not outcome.assigned:
        return outcome.slug, None
    # `apply_provider_category` stamps `updated_by` with the acting user,
    # and a cron-shaped job has none. Writing NULL would erase whoever
    # last edited the part, which is worse than leaving the column alone.
    part.updated_by = previous_editor
    # `apply_provider_category` writes `part.category_id` in place and no
    # longer returns a copy of it, so the part is the one place to read it
    # from — the two could only disagree.
    return outcome.slug, Change(
        action=ACTION_CATEGORY,
        category_path=index.paths.get(part.category_id, "") if part.category_id else "",
    )


def _provider_category_text(
    rows: Sequence[CustomField], provider: str | None
) -> str | None:
    """The vendor's own category string, as this part happens to store it.

    A secondary writes `"{provider}:category"`; the bare `category` row is
    what older imports left behind. Its own namespace wins, so a part
    carrying both is filed from the provider that is actually linked.
    """
    wanted = [f"{provider}:category", "category"] if provider else ["category"]
    by_key = {
        row.key: row.value
        for row in rows
        if row.source == "provider" and row.archived_at is None
    }
    for key in wanted:
        value = (by_key.get(key) or "").strip()
        if value:
            return value
    return None


def _resolve_provider(part: Part, ws: Workspace) -> str | None:
    """Who wrote this part's un-namespaced provider rows.

    The part's own link first, then the workspace's primary. ``None``
    when neither names a provider we can build — `parts_provider`
    defaults to the literal `"none"` — and a canonical row must never be
    written without one (see `spec_normalize_rows`).
    """
    for name in ((part.linked_provider or "").strip(), (ws.parts_provider or "").strip()):
        if name in KNOWN_PROVIDER_NAMES:
            return name
    return None


# ---------------------------------------------------------------------------
# transactions, batching, queries
# ---------------------------------------------------------------------------
@contextmanager
def _batch_transaction(db: Session, *, apply: bool) -> Iterator[None]:
    """One batch's write boundary: committed on apply, rolled back on dry run.

    The dry run does the real mutations inside a SAVEPOINT and discards
    it, so the CSV it produces is written by the code that would apply
    it. A second, read-only implementation of the same rules is the thing
    this avoids — it could disagree with the writer, and the operator
    would never know which one was right.

    `run_job` rolls a dry run back as well, and that is the OUTER guard,
    not a duplicate of this one: it protects against a job that forgot to
    check the flag, while this one makes `normalize_specs` side-effect-free
    when it is called directly, and is what lets the apply path commit per
    batch so a killed run keeps what it finished.
    """
    if apply:
        yield
        db.commit()
        return
    savepoint = db.begin_nested()
    try:
        yield
    finally:
        savepoint.rollback()


def _workspaces(db: Session, workspace_id: UUID | None) -> list[Workspace]:
    """The workspaces to process, in id order.

    A `--workspace` that names nothing raises rather than reporting a
    clean run over zero workspaces: on the apply step those two outcomes
    print identically, and one of them means the operator's scope was a
    typo and the backfill they thought they ran did not happen.
    """
    stmt = select(Workspace).order_by(Workspace.id)
    if workspace_id is not None:
        stmt = stmt.where(Workspace.id == workspace_id)
    rows = list(db.execute(stmt).scalars())
    if workspace_id is not None and not rows:
        raise UnknownWorkspaceError(f"no workspace with id {workspace_id}")
    return rows


def _part_ids(db: Session, *, ws_id: UUID) -> list[UUID]:
    """Every part id in the workspace, ordered.

    Ids only, and read once before the batch loop: keyset-paginating a
    table while committing into it re-reads rows this run has already
    rewritten, and 324 prod parts' worth of UUIDs is 5 KB. The rows
    themselves are loaded a batch at a time, which is the part that would
    not fit.

    Archived parts are included on purpose. This is a one-off
    normalisation of the whole table; leaving a hidden part on legacy
    keys would surface the moment it is restored.
    """
    return list(
        db.execute(
            select(Part.id).where(Part.workspace_id == ws_id).order_by(Part.id)
        ).scalars()
    )


def _batches(part_ids: Sequence[UUID], size: int) -> Iterator[Sequence[UUID]]:
    for start in range(0, len(part_ids), size):
        yield part_ids[start : start + size]


def _parts(db: Session, *, ws_id: UUID, part_ids: Sequence[UUID]) -> list[Part]:
    return list(
        db.execute(
            select(Part)
            .where(Part.workspace_id == ws_id)
            .where(Part.id.in_(part_ids))
            .order_by(Part.id)
        ).scalars()
    )


def _rows_by_part(
    db: Session, *, ws_id: UUID, part_ids: Sequence[UUID]
) -> dict[UUID, list[CustomField]]:
    """Every `custom_fields` row on this batch's parts, archived included.

    One query for the batch, not one per part. Archived rows come too:
    `uq_cf_unique` has no partial predicate, so a retired row still owns
    its key and renaming onto it would be an IntegrityError.
    """
    rows = db.execute(
        select(CustomField)
        .where(CustomField.workspace_id == ws_id)
        .where(CustomField.object_type == "part")
        .where(CustomField.object_id.in_(part_ids))
        .order_by(CustomField.object_id, CustomField.key)
    ).scalars()
    by_part: dict[UUID, list[CustomField]] = {}
    for row in rows:
        by_part.setdefault(row.object_id, []).append(row)
    return by_part


# ---------------------------------------------------------------------------
# audit, summary, lock
# ---------------------------------------------------------------------------
def _audit(
    db: Session, *, ws_id: UUID, counts: Counter[str], canonical: set[str]
) -> None:
    """One row per workspace: counts and key names, never values.

    `audit_log.comment` is a low-sensitivity summary by invariant
    (CLAUDE.md). Key names say which specs moved; the values themselves
    are in the CSV, which stays on the operator's machine.
    """
    summary = " ".join(
        f"{name}={counts[name]}" for name in (*ACTIONS, *_EXTRA_COUNTERS)
    )
    audit_log_ids(
        db,
        workspace_id=ws_id,
        user_id=None,
        action=AUDIT_ACTION,
        target_type="workspace",
        target_ids=[ws_id],
        comment=f"job={JOB_NAME} {summary} canonical={_key_list(sorted(canonical))}",
    )


def _key_list(keys: Sequence[str]) -> str:
    if len(keys) <= _AUDIT_KEY_LIMIT:
        return ",".join(keys)
    return ",".join(keys[:_AUDIT_KEY_LIMIT]) + f",+{len(keys) - _AUDIT_KEY_LIMIT}"


def _rank_unmapped(
    unmapped: Counter[tuple[str, str]],
) -> tuple[tuple[str, str, int], ...]:
    """The keys the schema had no home for, most frequent first.

    Tied counts break on `(slug, key)` so two runs over the same data
    produce the same file.
    """
    ranked = sorted(unmapped.items(), key=lambda item: (-item[1], item[0]))
    return tuple(
        (slug, key, count) for (slug, key), count in ranked[:UNMAPPED_REPORT_LIMIT]
    )


_LOCK_KEY = JOB_NAME


def _try_acquire_lock(db: Session) -> bool:
    """Take the SESSION-level advisory lock guarding this job.

    `run_job` wraps every job in a transaction-scoped lock, which
    Postgres drops at the first COMMIT — and this job commits per batch.
    Two concurrent runs would interleave two half-written re-keys over
    the same rows, so the lock has to outlive those commits. Same
    reasoning, and the same shape, as the datasheet backfill (ADR-0033).

    Known limit, shared with that precedent: the lock lives on whichever
    pooled connection the Session holds, and a commit hands that
    connection back. A pool that returns a different one on the next
    batch leaves the run unlocked and unlocks a connection that never
    held the lock (a Postgres WARNING, not an error). At prod's scale —
    two batches, seconds — the connection is not recycled in between.
    Fix the pattern before reusing it for a job that runs for hours.
    """
    return bool(
        db.execute(
            text(
                "SELECT pg_try_advisory_lock("
                "CAST(:classid AS int4), CAST(hashtext(:key) AS int4)"
                ")"
            ),
            {"classid": SPEC_NORMALIZE_LOCK_CLASSID, "key": _LOCK_KEY},
        ).scalar()
    )


def _release_lock(db: Session) -> None:
    db.execute(
        text(
            "SELECT pg_advisory_unlock("
            "CAST(:classid AS int4), CAST(hashtext(:key) AS int4)"
            ")"
        ),
        {"classid": SPEC_NORMALIZE_LOCK_CLASSID, "key": _LOCK_KEY},
    )
