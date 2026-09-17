"""The `provider-refresh` operator job — re-ask, and fill in the gaps.

Every part in this catalogue was imported once and then left alone.
Prod is 329 active parts, 285 of them linked, 290 links across two
providers, 118 with no category at all — and the parts imported earliest
carry the least, because the importer has grown a category resolver
(ADR-0034), a spec normaliser and a local datasheet store since. This
job re-runs the provider lookup for every linked part and writes back
whatever the providers answer now, through exactly the same service the
refresh route uses (`provider_refresh.py::refresh_part`), so no rule
about tiers, namespaces or ownership is decided twice.

Four properties make a bulk re-import over a live catalogue defensible:

* **`--dry-run` is the default and writes nothing.** The provider
  lookups are done for real — they are reads, and they warm the cache
  the apply will use — but every DB write lands in a SAVEPOINT that is
  rolled back. The CSV is produced by the code that would apply it.
* **it is throttled and it stops when told to.** DigiKey and Mouser both
  cap the free tier near 1,000 calls a day; 290 links at the default
  750 ms is about four minutes. A rate-limited answer ends the sweep
  rather than burning the rest of the run against a provider that is
  refusing (`ProviderQuotaExhausted`, exit 3).
* **it is resumable.** One commit per batch of parts on apply, so a run
  killed — or halted by quota — keeps everything it finished, and the
  CSV says how far it got.
* **it does not undo `spec-normalize`.** Archived junk stays archived
  and `custom_fields.provider` stamps stay put, because the reconcile it
  delegates to is the one that wrote those rules.

The primary tier downloads a part's image and datasheet as it goes, and
it does so through `fetch_provider_asset` — the allow-listed,
un-throttled entry point, NOT the backfill's `allow_any_host` one
(ADR-0033). The courtesy gap between CDN requests is the sweep's own
`--sleep-ms`, which is paced per part rather than per host; that is
adequate here and would not be inside a request handler, which is why
the relaxed path stays opt-in and stays out of this job.

`updated_by` is left NULL on everything this job touches, the same
choice `part-rename` made and for the same reason: crediting a bulk
sweep to whoever last edited the part would be a lie, and there is no
system actor to name instead. The `audit_log` row is where "a job did
this" is recorded — one per workspace for the sweep, on top of the
per-part rows the refresh itself writes.

Which parts a sweep touches, and the SAVEPOINT-or-COMMIT boundary around
each batch of writes, live next door in `provider_refresh_scope.py` —
the seam the 800-line ceiling was split on, and the one place to read
"what counts as a linked part".

ADR-0021 owns the job registry; ADR-0031 the tiers; ADR-0034 the specs.
See `docs/runbooks/provider-refresh.md`.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TextIO
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.advisory_locks import PROVIDER_REFRESH_LOCK_CLASSID
from app.domain.audit.service import log_ids as audit_log_ids
from app.domain.categories.service import CategoryIndex, category_index
from app.domain.parts.models import Part, PartProviderLink
from app.domain.parts.provider_credentials import active_credential_rows
from app.domain.parts.services.provider_refresh import (
    ProviderTarget,
    RefreshError,
    RefreshOutcome,
    primary_provider_name,
    provider_target,
    refresh_part,
)
from app.domain.parts.services.provider_refresh_report import (
    ACTION_ERROR,
    ACTION_LINKED,
    ACTION_MISS,
    ACTION_REFRESHED,
    ACTION_SKIPPED,
    ACTIONS,
    REPORT_COLUMNS,
    TIER_PRIMARY,
    TIER_SECONDARY,
    RefreshReport,
    RefreshRow,
)
from app.domain.parts.services.provider_refresh_scope import (
    UnknownWorkspaceError,
    batch_transaction,
    batches,
    links_by_part,
    part_ids_in_scope,
    parts_by_id,
    workspaces_in_scope,
)
from app.domain.parts.services.spec_normalize_report import rank_unmapped
from app.domain.provider_errors import ProviderError
from app.domain.workspaces.models import Workspace

logger = logging.getLogger(__name__)

__all__ = [
    "ACTION_ERROR",
    "ACTION_LINKED",
    "ACTION_MISS",
    "ACTION_REFRESHED",
    "ACTION_SKIPPED",
    "AUDIT_ACTION",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_SLEEP_MS",
    "JOB_NAME",
    "REPORT_COLUMNS",
    "ProviderQuotaExhausted",
    "SweepOutcome",
    # Defined in `provider_refresh_scope.py` and re-exported here: it is
    # raised by a `--workspace` that names nothing, which is a fact about
    # the JOB, and callers should not have to know which of the two
    # modules the query happens to live in.
    "UnknownWorkspaceError",
    "refresh_linked_parts",
]

JOB_NAME = "provider-refresh"
AUDIT_ACTION = "part.providers_swept"

#: Parts per transaction on apply. Small — each part costs one network
#: round trip per provider, so a batch is seconds of wall clock, and the
#: point of committing often is that a run cut short keeps what it did.
DEFAULT_BATCH_SIZE = 25

#: Milliseconds between provider calls. DigiKey and Mouser free tiers sit
#: near 1,000 calls a day; this is slow enough to be polite and fast
#: enough that prod's 290 links finish in about four minutes.
DEFAULT_SLEEP_MS = 750

#: What a provider says when the daily allowance is gone. Matched against
#: the message of a `found: False` answer AND of a raised `ProviderError`,
#: because the two providers disagree about which one a 429 is: DigiKey
#: returns `{"found": False, "message": "DigiKey rate limit reached"}`
#: while a transport-level 429 arrives as an exception.
_QUOTA_TOKENS: tuple[str, ...] = (
    "rate limit",
    "rate-limit",
    "ratelimit",
    "too many request",
    "quota",
    "calls exceeded",
    "limit exceeded",
    "429",
)


class ProviderQuotaExhausted(RuntimeError):
    """A provider refused for quota, so the sweep stopped.

    Raised only AFTER the report is finished and (on apply) the work is
    committed: the operator gets everything the run achieved plus a
    reason, not a traceback over a half-written file. `run_job` turns it
    into exit 3.

    It carries the `SweepOutcome` the run had reached, because raising is
    how this function reports a halt and a caller that wanted the counts
    would otherwise have only the CSV to parse for them.
    """

    def __init__(
        self, provider: str, message: str, *, outcome: "SweepOutcome | None" = None
    ) -> None:
        super().__init__(
            f"{JOB_NAME} stopped: provider {provider!r} is rate-limited or out of "
            f"quota ({message}). Everything finished before this point was kept; "
            "re-run tomorrow, or with --limit, to continue."
        )
        self.provider = provider
        self.detail = message
        self.outcome = outcome


@dataclass(frozen=True)
class SweepOutcome:
    """What the run did, or would have done."""

    applied: bool
    #: Parts in scope that were actually visited.
    parts: int
    #: Lines whose action was `refreshed` or `linked` — the work done.
    refreshed: int
    #: Every action, summed across workspaces and providers. Missing keys
    #: read as 0 — it is a `Counter`.
    counts: Mapping[str, int]
    per_provider: Mapping[tuple[UUID, str], Mapping[str, int]]
    #: `(category_slug, raw_key, count)`, most frequent first.
    unmapped: tuple[tuple[str, str, int], ...]
    #: The provider that ran out, when the sweep was cut short.
    halted_on: str | None


def refresh_linked_parts(
    db: Session,
    *,
    apply: bool = False,
    workspace_id: UUID | None = None,
    stream: TextIO | None = None,
    limit: int | None = None,
    only_uncategorized: bool = False,
    link_missing_providers: bool = False,
    sleep_ms: int = DEFAULT_SLEEP_MS,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> SweepOutcome:
    """Re-run every linked part's MPN against every provider that knows it.

    `workspace_id` limits the run to one workspace; omitted, every
    workspace is processed in id order. `limit` caps the number of PARTS
    across the whole run, which is what an operator means by "try ten
    first". `stream` is where the review CSV goes — the file `--report`
    named, or ``None`` for stdout; the CLI opens and closes it
    (`run_job._report_stream`).

    Caller owns the session. On apply this commits per batch, so it takes
    a SESSION-level advisory lock rather than relying on `run_job`'s
    transaction-scoped one, which Postgres drops at the first COMMIT.
    Two concurrent sweeps would spend the day's quota twice.

    Raises `ProviderQuotaExhausted` when a provider refuses for quota —
    after the report is written and the finished work committed.
    """
    if not _try_acquire_lock(db):
        logger.info("%s skipped: another run holds the lock", JOB_NAME)
        return SweepOutcome(apply, 0, 0, Counter(), {}, (), None)
    try:
        return _run(
            db,
            apply=apply,
            workspace_id=workspace_id,
            stream=stream,
            limit=limit,
            only_uncategorized=only_uncategorized,
            link_missing_providers=link_missing_providers,
            sleep_ms=sleep_ms,
            batch_size=batch_size,
        )
    except ProviderQuotaExhausted:
        # A planned stop, not a failure: the session is healthy and the
        # work is already committed, so rolling back here would only
        # discard the report's last flush-worth of truth.
        raise
    except Exception:
        # Ahead of the unlock, not after it: the unlock is a statement,
        # and a statement on a session left in a failed transaction
        # raises `PendingRollbackError` — which would replace whatever
        # actually went wrong with a message about the lock.
        db.rollback()
        raise
    finally:
        _release_lock(db)


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------
class _Throttle:
    """`time.sleep` BETWEEN provider calls, not after them.

    The first lookup of a run pays nothing, so a one-part sweep never
    sleeps at all. `sleep_ms=0` turns it off entirely, which is what the
    tests and a re-run against a warm cache want.

    It bounds the calls this job makes, not the ones a provider client
    makes internally: DigiKey may spend an OAuth token request and a
    fallback keyword search on top of the lookup itself.
    """

    def __init__(self, sleep_ms: int) -> None:
        self._seconds = max(sleep_ms, 0) / 1000
        self._called = False

    def wait(self) -> None:
        if self._called and self._seconds:
            time.sleep(self._seconds)
        self._called = True


@dataclass
class _Halt:
    """Set when a provider says the day's quota is gone."""

    provider: str | None = None
    detail: str = ""

    def __bool__(self) -> bool:
        return self.provider is not None


def _run(
    db: Session,
    *,
    apply: bool,
    workspace_id: UUID | None,
    stream: TextIO | None,
    limit: int | None,
    only_uncategorized: bool,
    link_missing_providers: bool,
    sleep_ms: int,
    batch_size: int,
) -> SweepOutcome:
    report = RefreshReport(stream)
    throttle = _Throttle(sleep_ms)
    halt = _Halt()
    per_provider: dict[tuple[UUID, str], Counter[str]] = {}
    unmapped: Counter[tuple[str, str]] = Counter()
    parts_seen = 0
    remaining = limit

    for ws in workspaces_in_scope(db, workspace_id):
        if remaining is not None and remaining <= 0:
            break
        targets = _targets_for(db, ws)
        index = category_index(db, ws_id=ws.id)
        part_ids = part_ids_in_scope(
            db, ws_id=ws.id, only_uncategorized=only_uncategorized, limit=remaining
        )
        if remaining is not None:
            remaining -= len(part_ids)
        counts: Counter[str] = Counter()

        done = 0
        for batch in batches(part_ids, batch_size):
            with batch_transaction(db, apply=apply):
                # Parts actually VISITED, which is not `len(batch)` when the
                # quota ran out part-way through it. The audit row and the
                # outcome both say how far the run got, so they have to
                # count what happened rather than what was queued.
                done += _process_batch(
                    db,
                    ws=ws,
                    part_ids=batch,
                    targets=targets,
                    index=index,
                    link_missing_providers=link_missing_providers,
                    report=report,
                    counts=counts,
                    per_provider=per_provider,
                    unmapped=unmapped,
                    throttle=throttle,
                    halt=halt,
                )
            report.flush()
            logger.info(
                "%s workspace=%s parts=%d/%d %s apply=%s",
                JOB_NAME,
                ws.id,
                done,
                len(part_ids),
                _summary(counts),
                apply,
            )
            if halt:
                break

        parts_seen += done
        if apply and sum(counts.values()):
            _audit(db, ws_id=ws.id, parts=done, counts=counts, targets=targets)
            db.commit()
        if halt:
            break

    ranked = rank_unmapped(unmapped)
    report.write_summary(per_provider=per_provider, unmapped=ranked)

    totals: Counter[str] = Counter()
    for counts in per_provider.values():
        totals.update(counts)
    logger.info(
        "%s done apply=%s parts=%d %s halted_on=%s",
        JOB_NAME,
        apply,
        parts_seen,
        _summary(totals),
        halt.provider,
    )
    outcome = SweepOutcome(
        applied=apply,
        parts=parts_seen,
        refreshed=totals[ACTION_REFRESHED] + totals[ACTION_LINKED],
        counts=totals,
        per_provider=per_provider,
        unmapped=ranked,
        halted_on=halt.provider,
    )
    if halt and halt.provider is not None:
        raise ProviderQuotaExhausted(halt.provider, halt.detail, outcome=outcome)
    return outcome


def _process_batch(
    db: Session,
    *,
    ws: Workspace,
    part_ids: Sequence[UUID],
    targets: Mapping[str, ProviderTarget],
    index: CategoryIndex,
    link_missing_providers: bool,
    report: RefreshReport,
    counts: Counter[str],
    per_provider: dict[tuple[UUID, str], Counter[str]],
    unmapped: Counter[tuple[str, str]],
    throttle: _Throttle,
    halt: _Halt,
) -> int:
    """Returns how many parts were visited — fewer than the batch when the
    quota ran out inside it."""
    parts = parts_by_id(db, ws_id=ws.id, part_ids=part_ids)
    links = links_by_part(db, ws_id=ws.id, part_ids=part_ids)
    primary = primary_provider_name(ws)
    visited = 0
    for part in parts:
        visited += 1
        order = _provider_order(
            part,
            links=links.get(part.id, ()),
            targets=targets,
            primary=primary,
            link_missing_providers=link_missing_providers,
        )
        for provider_name, already_linked in order:
            row = _refresh_one(
                db,
                ws=ws,
                part=part,
                provider_name=provider_name,
                already_linked=already_linked,
                target=targets.get(provider_name),
                primary=primary,
                index=index,
                unmapped=unmapped,
                throttle=throttle,
                halt=halt,
            )
            report.write(row)
            counts[row.action] += 1
            per_provider.setdefault((ws.id, provider_name), Counter())[row.action] += 1
            if halt:
                return visited
    return visited


def _refresh_one(
    db: Session,
    *,
    ws: Workspace,
    part: Part,
    provider_name: str,
    already_linked: bool,
    target: ProviderTarget | None,
    primary: str | None,
    index: CategoryIndex,
    unmapped: Counter[tuple[str, str]],
    throttle: _Throttle,
    halt: _Halt,
) -> RefreshRow:
    """One (part, provider) pair. Never raises for one part's problem."""
    category = _path(index, part.category_id)
    base = dict(
        workspace_id=ws.id,
        part_id=part.id,
        # The MPN we LOOKED UP. The primary may rewrite `parts.mpn` from
        # the payload, and the column that says which question was asked
        # has to survive the answer.
        mpn=part.mpn or "",
        provider=provider_name,
        # From the name rather than from `target.is_primary`, so a row we
        # could not build a client for still says which tier it would
        # have run as. `provider_target` derives it the same way.
        tier=TIER_PRIMARY if provider_name == primary else TIER_SECONDARY,
        category_before=category,
        # Overwritten below when a refresh actually moves it. Equal to
        # `category_before` everywhere else, because a blank cell next to
        # a filled one reads as "the sweep cleared the category".
        category_after=category,
    )
    if target is None:
        # Linked to a provider this workspace has no usable key for.
        # Nothing to ask, and not an error — the operator removed the key.
        return RefreshRow(
            **base,
            action=ACTION_SKIPPED,
            error="no credentials configured for this provider",
        )

    throttle.wait()
    try:
        outcome = refresh_part(
            db,
            ws=ws,
            part=part,
            provider_name=provider_name,
            user_id=None,
            category_index=index,
            target=target,
            # A provider the part is NOT linked to yet may only claim it
            # on an exact MPN: DigiKey falls back to a fuzzy keyword
            # search and Mouser matches partially, and a near miss here
            # would link the part to a different product and import its
            # specs. A provider that already owns the link keeps the
            # route's behaviour.
            require_exact_mpn=not already_linked,
        )
    except ProviderError as exc:
        message = exc.message or str(exc)
        if _looks_like_quota(message) or exc.status_code == 429:
            halt.provider, halt.detail = provider_name, message
        return RefreshRow(**base, action=ACTION_ERROR, error=message)

    if not outcome.found:
        message = outcome.error or "no match"
        if _looks_like_quota(message):
            # DigiKey reports a 429 as a clean `found: False`. Reading it
            # as a miss would mark every remaining part "the provider has
            # never heard of this" and record a catalogue-wide lie.
            halt.provider, halt.detail = provider_name, message
            return RefreshRow(**base, action=ACTION_ERROR, error=message)
        return RefreshRow(**base, action=ACTION_MISS, error=message)

    slug = outcome.category.slug if outcome.category else None
    if outcome.report is not None:
        unmapped.update((slug or "", key) for key in outcome.report.unmapped)
    return _refreshed_row(
        base, outcome, index=index, part=part, already_linked=already_linked
    )


def _refreshed_row(
    base: dict,
    outcome: RefreshOutcome,
    *,
    index: CategoryIndex,
    part: Part,
    already_linked: bool,
) -> RefreshRow:
    """`linked` is about the ASSOCIATION, not the row.

    `outcome.linked` says a `part_provider_links` row was created, and on
    prod that is true for most parts on the first sweep: 285 carry
    `parts.linked_provider` while the table holds 290 rows in total, so
    plenty of parts name a provider in the column and have no row for it.
    Calling those `linked` would report a catalogue-wide adoption event
    for what is a backfill of a row the column already implied. `linked`
    is reserved for a provider that had no claim on the part at all,
    which only `--link-missing-providers` produces.
    """
    report = outcome.report
    return RefreshRow(
        **{**base, "category_after": _path(index, part.category_id)},
        action=ACTION_REFRESHED if already_linked else ACTION_LINKED,
        part_columns_changed=outcome.part_columns_changed,
        specs_added=report.added if report else 0,
        specs_updated=report.updated if report else 0,
        specs_restored=report.restored if report else 0,
        specs_removed=(report.removed + report.archived) if report else 0,
        assets_fetched=outcome.assets_fetched,
    )


def _provider_order(
    part: Part,
    *,
    links: Sequence[PartProviderLink],
    targets: Mapping[str, ProviderTarget],
    primary: str | None,
    link_missing_providers: bool,
) -> list[tuple[str, bool]]:
    """`(provider, already_linked)` for one part, primary first.

    The part's `part_provider_links` rows and its `linked_provider`
    column are both read: the column is the primary's own record and
    predates the table, and a part carrying one without the other is
    exactly the drift this sweep exists to close.

    `--link-missing-providers` appends the providers this workspace has
    credentials for that the part is NOT linked to. It does not widen the
    set of PARTS — a part nothing has ever linked is out of scope for
    this job either way.
    """
    linked = {row.provider for row in links}
    own = (part.linked_provider or "").strip().lower()
    if own:
        linked.add(own)
    order = [(name, True) for name in _primary_first(linked, primary)]
    if link_missing_providers:
        order += [
            (name, False)
            for name in _primary_first(set(targets) - linked, primary)
        ]
    return order


def _primary_first(names: set[str], primary: str | None) -> list[str]:
    """The primary, then everything else alphabetically.

    Primary first because it is the tier that owns the part's columns and
    may fill its category, and the category picks the spec schema every
    later provider's payload is read through.
    """
    rest = sorted(name for name in names if name != primary)
    return ([primary] if primary is not None and primary in names else []) + rest


def _targets_for(db: Session, ws: Workspace) -> dict[str, ProviderTarget]:
    """Every provider client this workspace can build, once.

    Built per workspace rather than per part on purpose:
    `DigiKeyProvider` caches its OAuth token on the instance, so a client
    rebuilt for each of 252 parts would spend 252 token requests out of
    the same daily allowance the lookups come from.

    A provider that cannot be built is simply absent — the part-level
    pass reports `skipped` for it, which is the honest answer to "you are
    linked to DigiKey and there is no DigiKey key here".
    """
    targets: dict[str, ProviderTarget] = {}
    primary = primary_provider_name(ws)
    names = [primary] if primary and primary != "none" else []
    names += [row.provider for row in active_credential_rows(db, ws.id)]
    for name in names:
        if name in targets:
            continue
        try:
            targets[name] = provider_target(db, ws, name)
        except RefreshError as exc:
            logger.info(
                "%s workspace=%s provider=%s unavailable: %s",
                JOB_NAME,
                ws.id,
                name,
                exc.message,
            )
    return targets


def _looks_like_quota(message: str) -> bool:
    lowered = (message or "").lower()
    return any(token in lowered for token in _QUOTA_TOKENS)


def _path(index: CategoryIndex, category_id: UUID | None) -> str:
    return index.paths.get(category_id, "") if category_id else ""


# ---------------------------------------------------------------------------
# audit, summary, lock
# ---------------------------------------------------------------------------
def _summary(counts: Mapping[str, int]) -> str:
    return " ".join(f"{action}={counts.get(action, 0)}" for action in ACTIONS)


def _audit(
    db: Session,
    *,
    ws_id: UUID,
    parts: int,
    counts: Mapping[str, int],
    targets: Mapping[str, ProviderTarget],
) -> None:
    """One row per workspace for the SWEEP: counts and provider names.

    `audit_log.comment` is a low-sensitivity summary by invariant
    (CLAUDE.md). Provider names are fixed vocabulary; nothing a provider
    or a user wrote reaches this string. The per-part detail is in the
    `part.specs_reconciled` rows the refresh itself writes, and the
    values are in the CSV, which stays on the operator's machine.
    """
    audit_log_ids(
        db,
        workspace_id=ws_id,
        user_id=None,
        action=AUDIT_ACTION,
        target_type="workspace",
        target_ids=[ws_id],
        comment=(
            f"job={JOB_NAME} parts={parts} {_summary(counts)} "
            f"providers={','.join(sorted(targets)) or 'none'}"
        ),
    )


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
