"""Re-key one part's existing provider `custom_fields` rows onto the
canonical spec schema (A5).

This is the half of `spec-normalize` that touches rows. It takes the
rows a part already has, hands them to `spec_schema.normalise` as if
they were a provider payload, and moves each one to where the schema
says it belongs. No session, no queries: the caller loads the rows and
owns the transaction, which is what lets the dry run roll a whole batch
back.

**Why not `reconcile_provider_specs`.** That function writes an
*upstream payload* onto a part, and its last pass deletes every row the
payload did not mention — correct there, catastrophic here, where the
payload IS the current database state and "absent" means nothing. It
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
  (`provider_fields.py::provider_owns_custom_field_row`), so writing one
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

    remaining = _retire_junk(live, changes)
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
        by_raw_key = norm.by_raw_key
        for key, value in norm.canonical.items():
            source_row = by_raw_key.get(value.raw_key)
            if source_row is not None:
                _offer(contests, key, _Candidate(value, provider, source_row))
        superseded.extend(
            row
            for row in (by_raw_key.get(raw_key) for raw_key in norm.dropped)
            if row is not None
        )
        unmapped.extend((slug_label, key) for key in norm.optional)

    canonical, kept_manual, kept_other = _apply_canonical(part_rows, contests, changes)
    _archive_superseded(superseded, changes)
    return PartOutcome(
        changes=tuple(changes),
        kept_manual=kept_manual,
        kept_other_provider=kept_other,
        unattributed=unattributed,
        unmapped=tuple(unmapped),
        canonical=tuple(canonical),
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
    #: The row each payload key came from, first-wins — the same rule
    #: `spec_schema._partition` applies to a repeated key.
    by_raw_key: dict[str, CustomField]


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
) -> list[CustomField]:
    """Archive customs codes and placeholder values; return the rest.

    Deliberately ahead of, and independent of, provider attribution: a
    TARIC code is not a spec whoever wrote it, and a part nobody can
    attribute still deserves to lose its ~1,000 `-` rows.
    """
    remaining: list[CustomField] = []
    for row in live:
        bare_key = _bare_key(row.key)
        if is_junk_key(bare_key):
            _archive(row)
            changes.append(_retired(row, ACTION_ARCHIVE))
        elif is_junk_value(row.value):
            _archive(row)
            changes.append(_retired(row, ACTION_DROP))
        else:
            remaining.append(row)
    return remaining


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
    to one payload key and `normalise` keeps the first.
    """
    by_raw_key: dict[str, CustomField] = {}
    payload: list[tuple[str, str]] = []
    for row in sorted(rows, key=lambda r: r.key):
        bare_key = _bare_key(row.key)
        by_raw_key.setdefault(bare_key, row)
        payload.append((bare_key, row.value or ""))
    norm = normalise(category_slug, provider, payload)
    return _GroupNorm(
        canonical=norm.canonical,
        optional=norm.optional,
        dropped=norm.dropped,
        by_raw_key=by_raw_key,
    )


def _apply_canonical(
    part_rows: Sequence[CustomField],
    contests: dict[str, _Candidate],
    changes: list[Change],
) -> tuple[list[str], int, int]:
    """Write each canonical winner, and say what was left to somebody else."""
    by_key = {row.key: row for row in part_rows}
    canonical: list[str] = []
    kept_manual = kept_other = 0

    for key in sorted(contests):
        candidate = contests[key]
        source_row = candidate.source_row
        target = by_key.get(key)

        if target is None:
            # Rename in place: one UPDATE, and the row keeps its id and
            # its created_at rather than being retired next to a copy.
            change = _rekey(source_row, candidate, new_key=key)
            by_key[key] = source_row
            changes.append(change)
            canonical.append(key)
        elif target is source_row:
            action = _write_canonical(target, candidate)
            if action is not None:
                changes.append(_rekeyed(target, candidate, action, old_key=key))
            canonical.append(key)
        elif target.source != "provider":
            # The user owns this key. Leave both rows exactly as they are.
            kept_manual += 1
        elif not provider_outranks(candidate.provider, target.provider):
            kept_other += 1
        else:
            old_value = target.value or ""
            if _write_canonical(target, candidate) is not None:
                changes.append(
                    Change(
                        action=ACTION_REKEY,
                        key=key,
                        old_key=source_row.key,
                        provider=candidate.provider,
                        old_value=old_value,
                        new_value=target.value or "",
                    )
                )
            _archive(source_row)
            changes.append(_retired(source_row, ACTION_ARCHIVE))
            canonical.append(key)
    return canonical, kept_manual, kept_other


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
    """
    display = truncate_provider_field_value(candidate.value.display)
    value_changed = row.value != display
    num_changed = row.value_num != candidate.value.value_num
    provider_changed = row.provider != candidate.provider
    unarchived = row.archived_at is not None
    if not (value_changed or num_changed or provider_changed or unarchived):
        return None

    row.value = display
    row.value_num = candidate.value.value_num
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
    row.archived_at = utcnow()


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
