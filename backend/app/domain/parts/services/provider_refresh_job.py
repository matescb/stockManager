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
  lookups are done for real — they are reads, and doing them is what
  makes the CSV a plan rather than a guess — but every DB write lands in
  a SAVEPOINT that is rolled back and no asset is downloaded. The plan is
  produced by the code that would apply it. It does NOT make the apply
  cheaper: `provider_cache` is per-process and the apply is a separate
  `exec`, so budget two full passes against the day's allowance.
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

`--include-unlinked` widens the sweep to the parts NOTHING has linked —
34 on prod carry an MPN and no provider link at all, one carries neither.
It is the one place the PRIMARY tier runs on a part it has never owned,
which is defensible only because nobody owns that part's columns either:
the claim fills `manufacturer`, `footprint` and `description` where the
part is silent and leaves anything a human typed exactly as it is
(`refresh_part(claim_unowned=True)`). `--link-missing-providers`, by
contrast, still adds secondaries only — a part with a primary already has
one, and replacing it is a per-part human decision.

Which parts a sweep touches, which providers each one is asked about, and
the SAVEPOINT-or-COMMIT boundary around each batch of writes live next
door in `provider_refresh_scope.py` — the seam the 800-line ceiling was
split on, and the one place to read "what counts as a linked part". How a
failure is READ lives in `provider_refresh_failures.py`: whether a
provider message means the day's quota is gone, and what to call a write
the database rejected.

ADR-0021 owns the job registry; ADR-0031 the tiers; ADR-0034 the specs.
See `docs/runbooks/provider-refresh.md`.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from typing import TextIO
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.audit.service import log_ids as audit_log_ids
from app.domain.categories.service import CategoryIndex, category_index
from app.domain.parts.models import Part
from app.domain.parts.provider_credentials import active_credential_rows
from app.domain.parts.services.provider_refresh import (
    ProviderTarget,
    RefreshError,
    RefreshOutcome,
    primary_provider_name,
    provider_target,
    refresh_part,
)
from app.domain.parts.services.provider_refresh_failures import (
    looks_like_quota,
    rejected_write,
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
    SweepAlreadyRunning,
    UnknownWorkspaceError,
    batch_transaction,
    batches,
    links_by_part,
    part_ids_in_scope,
    parts_by_id,
    provider_order,
    sweep_lock,
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
    # Both defined in `provider_refresh_scope.py` with the lock they
    # guard, and re-exported here: a caller reaches for them because of
    # the JOB, and should not have to know which of the two modules the
    # advisory lock happens to live in.
    "SweepAlreadyRunning",
    "SweepOutcome",
    "sweep_lock",
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
    include_unlinked: bool = False,
    sleep_ms: int = DEFAULT_SLEEP_MS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    lock_held: bool = False,
) -> SweepOutcome:
    """Re-run every linked part's MPN against every provider that knows it.

    `workspace_id` limits the run to one workspace; omitted, every
    workspace is processed in id order. `limit` caps the number of PARTS
    across the whole run, which is what an operator means by "try ten
    first". `stream` is where the review CSV goes — the file `--report`
    named, or ``None`` for stdout; the CLI opens and closes it
    (`run_job_options.report_stream`).

    `include_unlinked` widens the scope to parts no provider has ever
    been linked to, where the primary may claim the part and fills only
    the columns it left empty. It is also the only way a part with NO
    MPN reaches this function, and such a part gets one `skipped` line
    naming the reason rather than being left out of the report.

    Caller owns the session. On apply this commits per batch, so it takes
    a SESSION-level advisory lock rather than relying on `run_job`'s
    transaction-scoped one, which Postgres drops at the first COMMIT.
    Two concurrent sweeps would spend the day's quota twice.

    `lock_held` says the caller is already inside `sweep_lock`. The CLI
    is, because it has to know whether the sweep may run BEFORE it opens
    — and truncates — the report file; a caller that does not care about
    that leaves it False and the lock is taken here.

    Raises `SweepAlreadyRunning` when another sweep holds the lock, and
    `ProviderQuotaExhausted` when a provider refuses for quota — the
    latter only after the report is written and the finished work
    committed.
    """
    with nullcontext() if lock_held else sweep_lock(db):
        return _run_guarded(
            db,
            apply=apply,
            workspace_id=workspace_id,
            stream=stream,
            limit=limit,
            only_uncategorized=only_uncategorized,
            link_missing_providers=link_missing_providers,
            include_unlinked=include_unlinked,
            sleep_ms=sleep_ms,
            batch_size=batch_size,
        )


def _run_guarded(
    db: Session,
    *,
    apply: bool,
    workspace_id: UUID | None,
    stream: TextIO | None,
    limit: int | None,
    only_uncategorized: bool,
    link_missing_providers: bool,
    include_unlinked: bool,
    sleep_ms: int,
    batch_size: int,
) -> SweepOutcome:
    """`_run` plus the rollback rule the advisory lock depends on."""
    try:
        return _run(
            db,
            apply=apply,
            workspace_id=workspace_id,
            stream=stream,
            limit=limit,
            only_uncategorized=only_uncategorized,
            link_missing_providers=link_missing_providers,
            include_unlinked=include_unlinked,
            sleep_ms=sleep_ms,
            batch_size=batch_size,
        )
    except ProviderQuotaExhausted:
        # A planned stop, not a failure: the session is healthy and the
        # work is already committed, so rolling back here would only
        # discard the report's last flush-worth of truth.
        raise
    except Exception:
        # Inside `sweep_lock`, so this runs BEFORE the unlock — and it has
        # to. The unlock is a statement, and a statement on a session left
        # in a failed transaction raises `PendingRollbackError`, which
        # would replace whatever actually went wrong with a message about
        # the lock.
        db.rollback()
        raise


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------
class _Throttle:
    """`time.sleep` BETWEEN provider calls, not after them.

    The first lookup of a run pays nothing, so a one-part sweep never
    sleeps at all. `sleep_ms=0` turns it off entirely, which is what the
    tests want, and an operator who knows the provider has headroom.

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
    include_unlinked: bool,
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
            db,
            ws_id=ws.id,
            only_uncategorized=only_uncategorized,
            limit=remaining,
            include_unlinked=include_unlinked,
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
                    include_unlinked=include_unlinked,
                    report=report,
                    counts=counts,
                    per_provider=per_provider,
                    unmapped=unmapped,
                    throttle=throttle,
                    halt=halt,
                    apply=apply,
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
    include_unlinked: bool,
    report: RefreshReport,
    counts: Counter[str],
    per_provider: dict[tuple[UUID, str], Counter[str]],
    unmapped: Counter[tuple[str, str]],
    throttle: _Throttle,
    halt: _Halt,
    apply: bool,
) -> int:
    """Returns how many parts were visited — fewer than the batch when the
    quota ran out inside it."""
    parts = parts_by_id(db, ws_id=ws.id, part_ids=part_ids)
    links = links_by_part(db, ws_id=ws.id, part_ids=part_ids)
    primary = primary_provider_name(ws)
    visited = 0
    for part in parts:
        visited += 1
        if not (part.mpn or "").strip():
            # Only `--include-unlinked` can put one here: the default
            # scope requires an MPN. There is nothing to ask any provider
            # about it and the operator is the one who has to supply the
            # MPN, so it gets a line naming the reason rather than being
            # silently absent from a run whose purpose was to find it.
            row = _no_mpn_row(ws_id=ws.id, part=part, index=index)
            report.write(row)
            counts[row.action] += 1
            per_provider.setdefault((ws.id, row.provider), Counter())[row.action] += 1
            continue
        order = provider_order(
            part,
            links=links.get(part.id, ()),
            providers_available=targets,
            primary=primary,
            link_missing_providers=link_missing_providers,
            include_unlinked=include_unlinked,
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
                apply=apply,
            )
            report.write(row)
            counts[row.action] += 1
            per_provider.setdefault((ws.id, provider_name), Counter())[row.action] += 1
            if halt:
                return visited
    return visited


def _no_mpn_row(*, ws_id: UUID, part: Part, index: CategoryIndex) -> RefreshRow:
    """The one line a part with no MPN gets, before any provider is asked.

    `provider` and `tier` are blank because no provider was asked and
    none would have been: naming one would put a skip in that provider's
    column of the summary for work it was never offered.
    """
    category = _path(index, part.category_id)
    return RefreshRow(
        workspace_id=ws_id,
        part_id=part.id,
        mpn="",
        provider="",
        tier="",
        action=ACTION_SKIPPED,
        category_before=category,
        category_after=category,
        error="part has no MPN to look up",
    )


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
    apply: bool,
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
        # One SAVEPOINT per (part, provider) pair, inside the batch's own
        # boundary. A statement Postgres rejects — an MPN rewrite that
        # collides with a sibling on `uq_parts_ws_mpn` is the realistic
        # one — aborts the whole transaction it runs in, so without this
        # one bad row would take every part in the batch with it and turn
        # a 25-part commit into a run that wrote nothing. Cheap at one
        # savepoint per pair for a job that sleeps 750 ms between calls.
        with db.begin_nested():
            outcome = refresh_part(
                db,
                ws=ws,
                part=part,
                provider_name=provider_name,
                user_id=None,
                category_index=index,
                target=target,
                # ALWAYS, linked or not. DigiKey falls back to a keyword
                # search when exact-match `ProductDetails` misses and Mouser
                # matches partially, so a "hit" is not necessarily this part:
                # a wrong one rewrites the canonical specs and can re-file
                # the part under another taxonomy. That risk is acceptable
                # when a human asked about one part by name and is reading
                # the answer; it is not acceptable unattended across 537
                # (part, provider) pairs. The cost is that a part whose MPN
                # is stored in a different format than the vendor prints it
                # reads as `miss` — which is a line in the CSV an operator
                # can act on, unlike a silent wrong match.
                require_exact_mpn=True,
                # The primary on a part it was not already linked to can only
                # be `--include-unlinked`: `provider_order` never offers the
                # primary for a part some provider already knows. Nobody owns
                # this part's columns, so the primary may claim it — and fills
                # only what the part left empty.
                claim_unowned=provider_name == primary and not already_linked,
                # A dry run downloads nothing. The file would land in
                # UPLOAD_DIR outside the savepoint this batch rolls back, so
                # a planning pass would leave content-addressed orphans and
                # spend ~570 HTTP requests to learn what the payload already
                # says. The report names them under `assets_would_fetch`.
                fetch_assets=apply,
            )
            # Force the pending UPDATE and INSERTs out INSIDE this
            # savepoint. `uq_parts_ws_mpn` is checked by the statement
            # that writes the row, and a violation that first surfaced at
            # the batch commit would be outside every savepoint there is.
            db.flush()
    except ProviderError as exc:
        message = exc.message or str(exc)
        if looks_like_quota(message) or exc.status_code == 429:
            halt.provider, halt.detail = provider_name, message
        return RefreshRow(**base, action=ACTION_ERROR, error=message)
    except IntegrityError as exc:
        # One part's problem, reported like a provider failure. The
        # realistic cause is the payload's spelling of the MPN colliding
        # with a sibling part on `uq_parts_ws_mpn` — two parts whose MPNs
        # differ only by case are legal until a refresh rewrites one of
        # them. The savepoint above has already been rolled back, so the
        # session is usable and the rest of the batch still commits. NOT
        # a halt: the provider is answering fine and every other part is
        # worth asking about.
        logger.info(
            "%s workspace=%s part=%s provider=%s write rejected: %s",
            JOB_NAME,
            ws.id,
            part.id,
            provider_name,
            exc,
        )
        return RefreshRow(**base, action=ACTION_ERROR, error=rejected_write(exc))

    if not outcome.found:
        message = outcome.error or "no match"
        if looks_like_quota(message):
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
    which two flags produce: `--link-missing-providers`, where a
    secondary joins a part the primary already owns, and
    `--include-unlinked`, where any tier claims a part nothing owned.
    Both only on an exact-MPN hit.
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
        assets_would_fetch=outcome.assets_would_fetch,
    )


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
