"""Re-key one part's existing provider `custom_fields` rows onto the
canonical spec schema (A5).

This is the half of `spec-normalize` that touches rows. It takes the
rows a part already has, hands them to `spec_schema.normalise` as if
they were a provider payload, and moves each one to where the schema
says it belongs. No session, no queries: the caller loads the rows and
owns the transaction, which is what lets the dry run roll a whole batch
back.

**Why not `reconcile_provider_specs`. Do not "fix" this by routing the
backfill through it.** That function writes an *upstream payload* onto a
part, and its last pass deletes every row the payload did not mention.
That is correct there and **a data-loss bug here**: the payload IS the
current database state, so "absent from the payload" describes no row
and the pass would delete whatever the schema happened not to claim. It
also re-namespaces a secondary's catalog keys, which on a prod table
whose 9,377 rows are all un-namespaced would duplicate them rather than
move them. So this module reuses `normalise()` — the alias table, the
junk denylist and the value parser are not re-implemented — and writes
the result under rules of its own:

* **nothing is deleted.** Junk, placeholders and superseded aliases get
  `archived_at`, which is recoverable and countable. ADR-0034 made that
  choice for the ingest path and it matters more here, where the rows
  being retired have been on the part for months.
* **nothing a user owns moves.** A `manual` or `override` row is
  invisible, and a provider row whose canonical key a user already owns
  is left exactly where it is — removing it would take away a spec that
  is on screen today and put nothing in its place.
* **a canonical row always names its provider.** A row with
  `provider IS NULL` is claimable by whoever refreshes next
  (`spec_schema.provider_outranks` treats an unstamped row as
  claimable), so writing one
  would hand a normalised value to the first provider through the door.
  When no provider can be named the canonical rewrite is skipped for
  that part and reported; junk is still retired, because a customs code
  is junk whoever wrote it.
* **the description is not mined.** `normalise()` will pull Mouser's
  parametric values out of prose, and a refresh wants that. A backfill
  does not: it would write specs with no existing row behind them, which
  is not what the operator reviewed in the CSV.

See ADR-0034 and `spec_normalize.py` for the job around this.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from app.core.time import utcnow
from app.domain.custom_fields.models import CustomField
from app.domain.parts.provider_fields import KNOWN_PROVIDER_NAMES
from app.domain.parts.services.provider_field_values import (
    truncate_provider_field_value,
)
from app.domain.parts.services.spec_normalize_report import (
    ACTION_ADD,
    ACTION_ARCHIVE,
    ACTION_DROP,
    ACTION_REKEY,
    ACTION_STAMP,
    ACTION_VALUE_NUM,
    Change,
)
from app.domain.parts.spec_schema import (
    SpecValue,
    all_canonical_keys,
    canonical_value,
    is_junk_key,
    is_junk_value,
    normalise,
    provider_outranks,
)

__all__ = ["PartOutcome", "normalize_part_rows"]

_NAMESPACE_SEPARATOR = ":"


@dataclass(frozen=True)
class PartOutcome:
    """What the pass did to one part. Counts and key names only."""

    changes: tuple[Change, ...] = ()
    #: Canonical keys a `manual` / `override` row already answers.
    kept_manual: int = 0
    #: Canonical keys a higher-precedence provider already answers.
    kept_other_provider: int = 0
    #: True when the part has provider rows but no provider can be named,
    #: so the canonical rewrite was skipped.
    unattributed: bool = False
    #: Raw keys the schema had no canonical home for, as
    #: `(category_slug, key)`. The list the alias table is extended from.
    unmapped: tuple[tuple[str, str], ...] = ()
    #: Canonical keys this part now carries, for the audit comment.
    canonical: tuple[str, ...] = field(default=())
    #: Rows that have to be INSERTED, unattached to any session — this
    #: module has none by design. The caller adds them inside its own
    #: batch transaction, so a dry run's savepoint discards them like
    #: every other mutation here. Only the schema's one-to-many alias
    #: produces any; see `_apply_canonical`.
    new_rows: tuple[CustomField, ...] = field(default=())


def normalize_part_rows(
    *,
    part_rows: Sequence[CustomField],
    category_slug: str | None,
    default_provider: str | None,
) -> PartOutcome:
    """Move one part's provider rows onto the canonical schema.

    `part_rows` is every `custom_fields` row on the part, archived and
    manual ones included — both are load-bearing. An archived row still
    occupies its key under `uq_cf_unique`, so renaming onto it would be
    an IntegrityError; a manual row is the answer this job must not
    overwrite.

    `default_provider` is who wrote the un-namespaced rows: the part's
    `linked_provider`, else the workspace's primary. ``None`` when
    neither names a provider we can build.
    """
    changes: list[Change] = []
    live = [r for r in part_rows if r.source == "provider" and r.archived_at is None]

    remaining, placeholders = _retire_junk(live, changes)
    settled, unkeyed = _split_already_canonical(remaining)
    groups, unattributed = _group_by_provider(unkeyed, default_provider)

    contests: dict[str, _Candidate] = {}
    superseded: list[CustomField] = []
    unmapped: list[tuple[str, str]] = []
    slug_label = category_slug or "-"

    for row in settled:
        provider = row.provider or _namespace_of(row.key) or default_provider
        if provider is None:
            unattributed = True
            continue
        key = _bare_key(row.key)
        value = canonical_value(category_slug, key, row.value or "")
        if value is None:
            # Canonical for some other category. Not this schema's row.
            continue
        _offer(contests, key, _Candidate(value, provider, row))

    for provider in sorted(groups):
        norm = _normalise_group(groups[provider], category_slug, provider)
        rows_by_raw_key = norm.rows_by_raw_key
        for key, value in norm.canonical.items():
            rows = rows_by_raw_key.get(value.raw_key, ())
            if rows:
                _offer(contests, key, _Candidate(value, provider, rows[0]))
                # Two DB rows can strip to one payload key — `Resistance`
                # next to `mouser:Resistance` on a workspace that promoted
                # Mouser to primary. Only one can hold the canonical key,
                # and leaving the other live would make it the sole answer
                # on the NEXT run, which would then change a value this run
                # already reported. Retiring it here is what keeps a second
                # run empty. Non-canonical keys are NOT collapsed this way:
                # `Features` and `mouser:Features` are two providers'
                # answers to one question and both are kept verbatim.
                superseded.extend(rows[1:])
        for raw_key in norm.dropped:
            superseded.extend(rows_by_raw_key.get(raw_key, ()))
        unmapped.extend((slug_label, key) for key in norm.optional)

    canonical, kept_manual, kept_other, new_rows = _apply_canonical(
        part_rows, contests, changes
    )
    _archive_superseded(superseded, changes)
    _retire_placeholders(placeholders, changes)
    return PartOutcome(
        changes=tuple(changes),
        kept_manual=kept_manual,
        kept_other_provider=kept_other,
        unattributed=unattributed,
        unmapped=tuple(unmapped),
        canonical=tuple(canonical),
        new_rows=tuple(new_rows),
    )


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Candidate:
    """One provider's answer to one canonical key, and the row it came from."""

    value: SpecValue
    provider: str
    source_row: CustomField


@dataclass(frozen=True)
class _GroupNorm:
    canonical: dict[str, SpecValue]
    optional: dict[str, str]
    dropped: list[str]
    #: Every row each payload key came from, in key order. A LIST, not one
    #: row: `spec_schema._partition` keeps the first of a repeated key
    #: because a payload duplicate costs nothing to discard, but here each
    #: one is a durable row that has to be accounted for.
    rows_by_raw_key: dict[str, list[CustomField]]


def _offer(
    contests: dict[str, _Candidate], key: str, candidate: _Candidate
) -> None:
    """Record one provider's answer to one canonical key, best wins.

    The same `provider_outranks` rule the refresh uses, so a part with
    both a DigiKey row and a Mouser one resolves here exactly as it would
    on its next refresh.

    A provider ties with itself, and the caller offers the rows already
    under a canonical key BEFORE the vendor spellings — so on a tie the
    vendor spelling wins and its row is the one that survives, collapsing
    a part carrying both `resistance` and `Resistance` onto one row. The
    other order would leave the duplicate in place forever, which is the
    thing this job exists to clear.
    """
    best = contests.get(key)
    if best is None or provider_outranks(candidate.provider, best.provider):
        contests[key] = candidate


def _split_already_canonical(
    rows: Sequence[CustomField],
) -> tuple[list[CustomField], list[CustomField]]:
    """Separate rows this schema has already re-keyed from the rest.

    A row keyed `resistance` is invisible to `normalise()` — the
    canonical key is not one of its own aliases — so it has to be
    recognised by key and re-parsed through `spec_schema.canonical_value`
    instead. Without this split the job would read its own output as
    unmapped free text and report every part as work still to do.
    """
    canonical_keys = all_canonical_keys()
    settled: list[CustomField] = []
    unkeyed: list[CustomField] = []
    for row in rows:
        (settled if _bare_key(row.key) in canonical_keys else unkeyed).append(row)
    return settled, unkeyed


def _retire_junk(
    live: Sequence[CustomField], changes: list[Change]
) -> tuple[list[CustomField], list[CustomField]]:
    """Archive customs codes; set placeholder rows aside. Returns
    `(rows with data, rows holding a placeholder)`.

    Deliberately ahead of, and independent of, provider attribution: a
    TARIC code is not a spec whoever wrote it, and a part nobody can
    attribute still deserves to lose its ~1,000 `-` rows.

    A junk KEY can be archived on the spot — no customs code is a
    canonical key, so nothing later in the pass can want it back. A junk
    VALUE cannot: the row's key may BE a canonical key, in which case a
    vendor spelling elsewhere on the part is about to fill it, and
    archiving it here would put a `drop` in the report for a row the same
    run revived. `_retire_placeholders` settles those at the end.
    """
    remaining: list[CustomField] = []
    placeholders: list[CustomField] = []
    for row in live:
        if is_junk_key(_bare_key(row.key)):
            _archive(row)
            changes.append(_retired(row, ACTION_ARCHIVE))
        elif is_junk_value(row.value):
            placeholders.append(row)
        else:
            remaining.append(row)
    return remaining, placeholders


def _retire_placeholders(
    rows: Sequence[CustomField], changes: list[Change]
) -> None:
    """Archive the `-` rows nothing filled in the meantime."""
    for row in rows:
        if row.archived_at is None and is_junk_value(row.value):
            _archive(row)
            changes.append(_retired(row, ACTION_DROP))


def _group_by_provider(
    rows: Sequence[CustomField], default_provider: str | None
) -> tuple[dict[str, list[CustomField]], bool]:
    """Split rows by who wrote them. Returns `(groups, unattributed)`.

    A `"{provider}:"` prefix names its own writer; everything else is the
    `default_provider`'s. Rows with neither are left untouched and the
    part is flagged, because stamping them would need a name we do not
    have.
    """
    groups: dict[str, list[CustomField]] = {}
    unattributed = False
    for row in rows:
        provider = _namespace_of(row.key) or default_provider
        if provider is None:
            unattributed = True
            continue
        groups.setdefault(provider, []).append(row)
    return groups, unattributed


def _normalise_group(
    rows: Sequence[CustomField], category_slug: str | None, provider: str
) -> _GroupNorm:
    """Hand one provider's rows to `spec_schema.normalise` as a payload.

    Sorted by key so a part that carries both `Resistance` and
    `mouser:Resistance` resolves the same way on every run — they strip
    to one payload key, `normalise` keeps the first, and the caller
    retires the rest.
    """
    rows_by_raw_key: dict[str, list[CustomField]] = {}
    payload: list[tuple[str, str]] = []
    for row in sorted(rows, key=lambda r: r.key):
        bare_key = _bare_key(row.key)
        rows_by_raw_key.setdefault(bare_key, []).append(row)
        payload.append((bare_key, row.value or ""))
    norm = normalise(category_slug, provider, payload)
    return _GroupNorm(
        canonical=norm.canonical,
        optional=norm.optional,
        dropped=norm.dropped,
        rows_by_raw_key=rows_by_raw_key,
    )


def _apply_canonical(
    part_rows: Sequence[CustomField],
    contests: dict[str, _Candidate],
    changes: list[Change],
) -> tuple[list[str], int, int, list[CustomField]]:
    """Write each canonical winner, and say what was left to somebody else.

    **A source row can only be renamed once.** `Size / Dimension` answers
    both `length` and `width` — the schema's one one-to-many alias — and
    the part has a single row carrying it. Renaming that row for `length`
    and then again for `width` leaves the part with `width` alone, the
    raw value gone, and a second run reporting nothing, which is what
    would make the loss invisible. So the first canonical key renames the
    row and every later key claiming the SAME row gets a copy to insert.
    """
    by_key = {row.key: row for row in part_rows}
    canonical: list[str] = []
    kept_manual = kept_other = 0
    #: Source rows an earlier canonical key already renamed, by identity —
    #: two distinct rows can carry equal values, and it is the ROW that
    #: can only move once.
    claimed: set[int] = set()
    new_rows: list[CustomField] = []

    for key in sorted(contests):
        candidate = contests[key]
        source_row = candidate.source_row
        target = by_key.get(key)

        if target is None and id(source_row) in claimed:
            row = _copy_for(source_row, candidate, new_key=key)
            new_rows.append(row)
            by_key[key] = row
            changes.append(
                Change(
                    action=ACTION_ADD,
                    key=key,
                    old_key=candidate.value.raw_key,
                    provider=candidate.provider,
                    old_value=candidate.value.raw_value,
                    new_value=row.value or "",
                )
            )
            canonical.append(key)
        elif target is None:
            # Rename in place: one UPDATE, and the row keeps its id and
            # its created_at rather than being retired next to a copy.
            change = _rekey(source_row, candidate, new_key=key)
            by_key[key] = source_row
            claimed.add(id(source_row))
            changes.append(change)
            canonical.append(key)
        elif target is source_row:
            action = _write_canonical(target, candidate)
            if action is not None:
                changes.append(_rekeyed(target, candidate, action, old_key=key))
            # It holds this canonical key now, so a later key that reads
            # the same row gets a copy rather than renaming this one away.
            claimed.add(id(source_row))
            canonical.append(key)
        elif target.source != "provider":
            # The user owns this key. Leave both rows exactly as they are.
            kept_manual += 1
        elif not provider_outranks(candidate.provider, target.provider):
            kept_other += 1
        else:
            # Two rows answer one canonical key. The value moves onto the
            # one already holding the key and the other is retired, which
            # is TWO changes to TWO rows — and each report line describes
            # exactly one of them. Naming the source row's key as this
            # line's `old_key` would read as a rename, and the runbook's
            # "set `key` back to `old_key`" reversal would then try to
            # rename the live row onto a key the archived one still owns
            # (`uq_cf_unique` has no partial predicate, so that fails).
            old_value = target.value or ""
            if _write_canonical(target, candidate) is not None:
                changes.append(
                    Change(
                        action=ACTION_REKEY,
                        key=key,
                        old_key=key,
                        provider=candidate.provider,
                        old_value=old_value,
                        new_value=target.value or "",
                    )
                )
            _archive(source_row)
            changes.append(_retired(source_row, ACTION_ARCHIVE))
            claimed.add(id(source_row))
            canonical.append(key)
    return canonical, kept_manual, kept_other, new_rows


def _copy_for(
    source_row: CustomField, candidate: _Candidate, *, new_key: str
) -> CustomField:
    """A second canonical row for a source row that has already moved.

    Unattached: this module takes no session. It carries the parsed value
    and sidecar for THIS key — the extractor gives `length` and `width`
    different numbers out of one string — and it is `source='provider'`
    with the provider named, because an unstamped canonical row is
    claimable by whoever refreshes next.

    `original_value` is deliberately left NULL. It means "what upstream
    said before a user edited this row", and nobody has edited a row that
    did not exist a moment ago.
    """
    return CustomField(
        workspace_id=source_row.workspace_id,
        object_type=source_row.object_type,
        object_id=source_row.object_id,
        key=new_key,
        value=truncate_provider_field_value(candidate.value.display),
        value_num=candidate.value.value_num,
        source="provider",
        provider=candidate.provider,
    )


def _archive_superseded(rows: Sequence[CustomField], changes: list[Change]) -> None:
    """Retire an alias that lost its canonical key to a better spelling.

    Unconditional, including when the canonical key went to a manual row:
    the loser is a second spelling of a question that now has one answer
    on the part, whoever supplied it.
    """
    for row in rows:
        if row.archived_at is not None:
            continue
        _archive(row)
        changes.append(_retired(row, ACTION_ARCHIVE))


def _rekey(row: CustomField, candidate: _Candidate, *, new_key: str) -> Change:
    old_key = row.key
    old_value = row.value or ""
    row.key = new_key
    _write_canonical(row, candidate)
    return Change(
        action=ACTION_REKEY,
        key=new_key,
        old_key=old_key,
        provider=candidate.provider,
        old_value=old_value,
        new_value=row.value or "",
    )


def _write_canonical(row: CustomField, candidate: _Candidate) -> str | None:
    """Put the parsed value, the sidecar and the provider on `row`.

    Returns the action that best names what moved, or ``None`` when the
    row already said all of it — which is what makes a second run a
    no-op.

    **The sidecar only moves when the display moves, or when there is no
    sidecar at all.** On a second run the candidate is a re-parse of the
    display this job wrote, and a display carries fewer significant
    digits than the raw vendor value the first run read: `1/3W` is stored
    as `333.3333 mW` with `value_num` 0.333333333333333333, and
    re-parsing the display gives 0.3333333. Overwriting on that
    difference would make every such row a change on every run, and
    `±0.00001%` — which displays as `0%` — would have its number
    replaced by zero.
    """
    display = truncate_provider_field_value(candidate.value.display)
    new_num = candidate.value.value_num
    value_changed = row.value != display
    num_changed = (
        row.value_num != new_num
        if value_changed
        else row.value_num is None and new_num is not None
    )
    provider_changed = row.provider != candidate.provider
    unarchived = row.archived_at is not None
    if not (value_changed or num_changed or provider_changed or unarchived):
        return None

    row.value = display
    if value_changed or num_changed:
        row.value_num = new_num
    row.provider = candidate.provider
    row.archived_at = None
    if value_changed or unarchived:
        return ACTION_REKEY
    if provider_changed:
        return ACTION_STAMP
    return ACTION_VALUE_NUM


def _rekeyed(
    row: CustomField, candidate: _Candidate, action: str, *, old_key: str
) -> Change:
    return Change(
        action=action,
        key=row.key,
        old_key=old_key,
        provider=candidate.provider,
        old_value=candidate.value.raw_value,
        new_value=row.value or "",
    )


def _retired(row: CustomField, action: str) -> Change:
    return Change(
        action=action,
        key=row.key,
        old_key=row.key,
        provider=row.provider or "",
        old_value=row.value or "",
    )


def _archive(row: CustomField) -> None:
    """Retire a row, and take its number out of the sortable index.

    `ix_custom_fields_ws_key_value_num` is partial on
    `value_num IS NOT NULL`, so a retired row that kept one stays in an
    index built to answer questions about live specs. The ingest path
    clears it for the same reason.
    """
    row.archived_at = utcnow()
    row.value_num = None


def _namespace_of(key: str) -> str | None:
    """The provider whose namespace `key` sits in, or ``None``.

    Only `KNOWN_PROVIDER_NAMES` count, for the reason
    `provider_fields.is_provider_namespaced_key` gives: an upstream spec
    genuinely called `Vref:max` is not in anybody's namespace.
    """
    for name in KNOWN_PROVIDER_NAMES:
        if key.startswith(f"{name}{_NAMESPACE_SEPARATOR}"):
            return name
    return None


def _bare_key(key: str) -> str:
    provider = _namespace_of(key)
    return key[len(provider) + 1:] if provider else key
