"""The boundaries a `provider-refresh` sweep runs inside.

Split out of `provider_refresh_job.py` for the 800-line ceiling, on the
seam that was already there: this module answers "may this sweep run at
all, which workspaces, which parts, which links, and when does a batch of
writes become permanent", and nothing in it knows what a refresh is or
what a provider says.

The scope rule is the load-bearing part. A part is in scope when it is
ACTIVE, has a non-blank MPN, and some provider already knows it — a
`part_provider_links` row OR the `parts.linked_provider` column, because
the column is the primary's own record, predates the table, and prod
carries parts with one and not the other. Widening it spends an API call
per part per provider on parts nothing has ever linked, which is why
`include_unlinked` is a flag the operator asks for rather than the
default.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import and_, func, not_, or_, select, text
from sqlalchemy.orm import Session

from app.core.advisory_locks import PROVIDER_REFRESH_LOCK_CLASSID
from app.domain.parts.models import Part, PartProviderLink
from app.domain.workspaces.models import Workspace

#: Duplicated from `provider_refresh_job.py` rather than imported: that
#: module imports this one, so the arrow only points one way. The two are
#: pinned together by `tests/test_provider_refresh_job.py`, which locks
#: this key by hand and expects the job to be refused.
JOB_NAME = "provider-refresh"

__all__ = [
    "SweepAlreadyRunning",
    "UnknownWorkspaceError",
    "batch_transaction",
    "batches",
    "links_by_part",
    "part_ids_in_scope",
    "parts_by_id",
    "provider_order",
    "sweep_lock",
    "workspaces_in_scope",
]


class SweepAlreadyRunning(RuntimeError):
    """Another `provider-refresh` holds the session-level lock.

    A refusal, not a result. Returning an empty outcome and exit 0 would
    read as "there was nothing to do", which for a job whose whole
    purpose is to change a few hundred parts is the most misleading
    answer available — and it would have already truncated the CSV the
    RUNNING sweep's operator is watching. The CLI takes this lock before
    it opens the report for exactly that reason, and reports it as a
    usage error (exit 2).
    """


class UnknownWorkspaceError(LookupError):
    """`--workspace` named a workspace that does not exist.

    A `LookupError` because that is the shape `cli/run_job.py::main`
    turns into a usage error and exit 2 for every operator-run job. The
    twins in `spec_normalize.py` and `part_rename.py` say the same thing.
    """


@contextmanager
def batch_transaction(db: Session, *, apply: bool) -> Iterator[None]:
    """One batch's write boundary: committed on apply, rolled back on dry run.

    The dry run does the real mutations inside a SAVEPOINT and discards
    it, so the CSV it produces is written by the code that would apply
    it. `run_job` rolls a dry run back as well, and that is the OUTER
    guard rather than a duplicate of this one: it protects against a job
    that forgot to check the flag, while this makes
    `refresh_linked_parts` side-effect-free when called directly, and is
    what lets the apply path commit per batch so a halted run keeps what
    it finished.
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


def workspaces_in_scope(db: Session, workspace_id: UUID | None) -> list[Workspace]:
    """The workspaces to process, in id order.

    A `--workspace` that names nothing raises rather than reporting a
    clean run over zero workspaces: on the apply step those two outcomes
    print identically, and one of them means the operator's scope was a
    typo and the sweep they thought they ran did not happen.
    """
    stmt = select(Workspace).order_by(Workspace.id)
    if workspace_id is not None:
        stmt = stmt.where(Workspace.id == workspace_id)
    rows = list(db.execute(stmt).scalars())
    if workspace_id is not None and not rows:
        raise UnknownWorkspaceError(f"no workspace with id {workspace_id}")
    return rows


def part_ids_in_scope(
    db: Session,
    *,
    ws_id: UUID,
    only_uncategorized: bool,
    limit: int | None,
    include_unlinked: bool = False,
) -> list[UUID]:
    """Active parts with an MPN that some provider already knows.

    "Knows" is a `part_provider_links` row OR the `parts.linked_provider`
    column: the column is the primary's own record and predates the
    table, and prod carries parts with one and not the other.

    `include_unlinked` adds the parts NOTHING knows — 34 on prod carrying
    an MPN, one carrying none. The MPN requirement is relaxed for those
    and only those, so the part with none reports as `skipped` with a
    reason rather than being silently absent from a run whose whole
    purpose was to find it; a LINKED part with a blank MPN stays out,
    because it is already in a provider's hands and there is nothing new
    to learn about it here.

    Archived parts are excluded — unlike `spec-normalize`, which
    re-keys rows in place and has no reason to leave a hidden part on
    legacy keys, this spends an API call per part and a hidden one is not
    worth one.

    Ids only, read once before the batch loop: keyset-paginating a table
    while committing into it re-reads rows this run has already
    rewritten, and a few hundred UUIDs is a few kilobytes.
    """
    linked_row = (
        select(PartProviderLink.id)
        .where(PartProviderLink.workspace_id == ws_id)
        .where(PartProviderLink.part_id == Part.id)
        .where(PartProviderLink.archived_at.is_(None))
    )
    known = or_(
        func.btrim(func.coalesce(Part.linked_provider, "")) != "",
        linked_row.exists(),
    )
    has_mpn = func.btrim(func.coalesce(Part.mpn, "")) != ""
    reachable = (
        or_(and_(known, has_mpn), not_(known)) if include_unlinked
        else and_(known, has_mpn)
    )
    stmt = (
        select(Part.id)
        .where(Part.workspace_id == ws_id)
        .where(Part.archived_at.is_(None))
        .where(reachable)
        .order_by(Part.id)
    )
    if only_uncategorized:
        stmt = stmt.where(Part.category_id.is_(None))
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(db.execute(stmt).scalars())


def provider_order(
    part: Part,
    *,
    links: Sequence[PartProviderLink],
    providers_available: Iterable[str],
    primary: str | None,
    link_missing_providers: bool,
    include_unlinked: bool,
) -> list[tuple[str, bool]]:
    """`(provider, already_linked)` for one part, primary first.

    The part's `part_provider_links` rows and its `linked_provider`
    column are both read: the column is the primary's own record and
    predates the table, and a part carrying one without the other is
    exactly the drift this sweep exists to close.

    **`--link-missing-providers` adds SECONDARIES only.** Adding the
    workspace's primary would run the primary path on a part it has never
    owned and rewrite `manufacturer`, `mpn`, `footprint`, `description`,
    `linked_provider` and `part_type` from a provider nobody chose for
    it — on icicle, where Mouser is primary and 252 of 290 links are
    DigiKey secondaries, that is most of the catalogue. It is also not
    reversible the way the runbook describes: `DELETE /provider-links/
    {provider}` refuses the primary, and releasing those columns is a
    `PATCH … unlink_provider=true` per part. Promoting a provider to a
    part's primary is a per-part human decision, taken through
    `POST /api/parts/{id}/refresh-from-provider`, not something a sweep
    does to 252 parts at once.

    **`--include-unlinked` is the one exception, and only on a part
    nothing owns.** A part with no link row and no `linked_provider`
    column has no primary to displace and no provider-written column to
    overwrite, so the primary may claim it — and it is the tier that
    fills a NULL category and downloads the datasheet, which is most of
    what the flag is for. It fills gaps only; see
    `provider_refresh.py::refresh_part` and its `claim_unowned`.

    The whole list is ordered primary-first in ONE sort rather than
    linked-then-unlinked, so the primary cannot end up behind a
    secondary: it is the tier that fills a NULL category, and the
    category picks the spec schema every later payload is read through.
    """
    linked = {row.provider for row in links}
    own = (part.linked_provider or "").strip().lower()
    if own:
        linked.add(own)
    candidates = dict.fromkeys(linked, True)
    available = set(providers_available)
    if link_missing_providers:
        for name in available - linked - ({primary} if primary else set()):
            candidates[name] = False
    if include_unlinked and not linked:
        for name in available:
            candidates.setdefault(name, False)
    return [
        (name, candidates[name])
        for name in sorted(candidates, key=lambda name: (name != primary, name))
    ]


def batches(part_ids: Sequence[UUID], size: int) -> Iterator[Sequence[UUID]]:
    for start in range(0, len(part_ids), size):
        yield part_ids[start : start + size]


def parts_by_id(db: Session, *, ws_id: UUID, part_ids: Sequence[UUID]) -> list[Part]:
    return list(
        db.execute(
            select(Part)
            .where(Part.workspace_id == ws_id)
            .where(Part.id.in_(part_ids))
            .order_by(Part.id)
        ).scalars()
    )


def links_by_part(
    db: Session, *, ws_id: UUID, part_ids: Sequence[UUID]
) -> dict[UUID, list[PartProviderLink]]:
    """Every live link on this batch's parts. One query, not one per part."""
    rows = db.execute(
        select(PartProviderLink)
        .where(PartProviderLink.workspace_id == ws_id)
        .where(PartProviderLink.part_id.in_(part_ids))
        .where(PartProviderLink.archived_at.is_(None))
        .order_by(PartProviderLink.part_id, PartProviderLink.provider)
    ).scalars()
    by_part: dict[UUID, list[PartProviderLink]] = {}
    for row in rows:
        by_part.setdefault(row.part_id, []).append(row)
    return by_part


@contextmanager
def sweep_lock(db: Session) -> Iterator[None]:
    """Hold the session-level advisory lock for the length of a sweep.

    Separate from `refresh_linked_parts` so the CLI can take it before it
    opens the report file: `report_stream` truncates on open, and a run
    that turns out not to be allowed to start must not have destroyed the
    CSV of the one that is still going.
    """
    if not _try_acquire_lock(db):
        raise SweepAlreadyRunning(
            f"another {JOB_NAME} is already running (it holds the advisory "
            "lock). Wait for it to finish — two sweeps would spend the day's "
            "provider quota twice and interleave writes over the same parts."
        )
    try:
        yield
    finally:
        _release_lock(db)


_LOCK_KEY = JOB_NAME


def _try_acquire_lock(db: Session) -> bool:
    """Take the SESSION-level advisory lock guarding this job.

    `run_job` wraps every job in a transaction-scoped lock, which
    Postgres drops at the first COMMIT — and this job commits per batch.
    Two concurrent sweeps would spend the day's provider quota twice and
    interleave two sets of writes over the same parts, so the lock has to
    outlive those commits. Same shape, and the same known limit about
    pooled connections, as `spec_normalize.py`.
    """
    return bool(
        db.execute(
            text(
                "SELECT pg_try_advisory_lock("
                "CAST(:classid AS int4), CAST(hashtext(:key) AS int4)"
                ")"
            ),
            {"classid": PROVIDER_REFRESH_LOCK_CLASSID, "key": _LOCK_KEY},
        ).scalar()
    )


def _release_lock(db: Session) -> None:
    db.execute(
        text(
            "SELECT pg_advisory_unlock("
            "CAST(:classid AS int4), CAST(hashtext(:key) AS int4)"
            ")"
        ),
        {"classid": PROVIDER_REFRESH_LOCK_CLASSID, "key": _LOCK_KEY},
    )
