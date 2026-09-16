"""Turn one provider payload into this part's `custom_fields` rows (A3).

The single writer for both paths that ingest a provider lookup — the
create path (`services/provider_import.py`) and the refresh route
(`api/routes/parts_refresh.py`). Before this module the two had separate,
subtly different copies of "write the specs", and neither normalised
anything: every `Parameters[]` row landed verbatim, which is why prod
carries 9,377 provider rows including 277 ECCN codes, 246 MSL codes and
about a thousand whose value is literally `-`.

What changes, and what deliberately does not:

* **canonical keys are normalised and SHARED.** `spec_schema.normalise`
  maps the payload onto the per-category schema, and the row is written
  under the canonical key (`resistance`) with a parsed display value and
  a `value_num` sidecar. BOTH tiers write these un-namespaced, because
  "load the specs from DigiKey and Mouser" is meaningless while a
  secondary's parametric data sits under a prefix nothing reads. Who
  wins a contested key is `spec_schema.PROVIDER_PRECEDENCE`, recorded in
  `custom_fields.provider` — not who refreshed last.
* **catalog and optional keys do not move.** Same key strings, same
  namespace rules ADR-0031 gave them (primary bare, secondary
  `"{provider}:"`-prefixed), because `web/src/lib/providerCatalog.ts`
  and the Sourcing tab key off those exact names.
* **junk is archived, stale data is deleted.** A customs code or a `-`
  value is never written, and an existing row that is one gets
  `archived_at` — recoverable and countable, unlike the hard delete the
  ordinary "absent from my payload" pass does.
* **nothing a user touched is overwritten.** `manual` rows are left
  alone entirely; an `override` keeps its value and only its
  `original_value` tracks upstream, so Restore still lands on what the
  vendor says now — the behaviour `_reconcile_provider_fields` had.

Category assignment lives here too (`apply_provider_category`), because
it has to happen BEFORE the specs: the category picks the schema, and
the schema decides whether DigiKey's `Temperature Coefficient` is a
resistor's ppm/°C figure or a ceramic capacitor's dielectric.

See ADR-0031 (namespaces), ADR-0034 (the schema).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence
from uuid import UUID

from sqlalchemy import select

from app.core.time import utcnow
from app.domain.audit.service import log_ids as _audit_log_ids
from app.domain.categories.service import (
    CategoryIndex,
    category_index,
    category_name_path,
    resolve_category_path_or_root,
)
from app.domain.custom_fields.models import CustomField
from app.domain.parts.provider_fields import (
    CUSTOM_FIELD_KEY_MAX,
    is_provider_namespaced_key,
    namespaced_custom_field_key,
    provider_wrote_custom_field_row,
)
from app.domain.parts.services.provider_field_values import (
    truncate_provider_field_value,
)
from app.domain.parts.spec_schema import (
    all_canonical_keys,
    category_for_provider,
    category_slug_for,
    is_junk_key,
    is_junk_value,
    normalise,
    provider_outranks,
)

__all__ = [
    "CategoryOutcome",
    "ReconcileReport",
    "apply_provider_category",
    "reconcile_provider_specs",
]

AUDIT_ACTION = "part.specs_reconciled"

# An audit comment is for a human reading a timeline. Past this many key
# names it stops being one, so the tail collapses to a count — the same
# grammar the EDA import uses for a 20-entry library.
_AUDIT_KEY_LIMIT = 20


@dataclass(frozen=True)
class CategoryOutcome:
    """What the provider's category string did to this part.

    `slug` is the spec schema to normalise with, and is deliberately
    filled from the SUGGESTION when the path did not resolve: knowing the
    part is a ceramic capacitor is useful even when this workspace has no
    category to file it under.
    """

    category_id: UUID | None
    assigned: bool
    suggestion: str | None
    slug: str | None


@dataclass(frozen=True)
class ReconcileReport:
    """Counts and key names — never values. Feeds the response summary
    (whose `added` / `updated` / `removed` / `skipped` shape the frontend
    reads) and the audit comment."""

    added: int = 0
    updated: int = 0
    removed: int = 0
    #: Fields this payload could not be written under: a namespaced key
    #: that would not fit `custom_fields.key`, and a bare key that spells
    #: something the payload already answered canonically.
    skipped: int = 0
    #: Junk keys and `-` values retired from a part that already had them.
    archived: int = 0
    #: Rows brought back out of `archived_at` because upstream answered
    #: their key again. Counted apart from `updated`: the value may not
    #: have moved at all, but the row reappearing IS the change.
    restored: int = 0
    #: Canonical keys this provider now owns on the part.
    canonical: tuple[str, ...] = ()
    #: Canonical keys left to a `manual` / `override` row.
    kept_manual: tuple[str, ...] = ()
    #: Canonical keys a higher-precedence provider already answered.
    kept_other_provider: tuple[str, ...] = ()
    #: Raw payload keys refused: junk, placeholder values, losing aliases.
    #: Surfaced in the refresh response so "the vendor sent it and we did
    #: not store it" is visible rather than merely absent.
    dropped: tuple[str, ...] = ()

    def summary(self) -> dict[str, int]:
        """The `summary` object the refresh response has always carried,
        plus the two counts A3 adds."""
        return {
            "added": self.added,
            "updated": self.updated,
            "removed": self.removed,
            "skipped": self.skipped,
            "archived": self.archived,
            "restored": self.restored,
            "dropped": len(self.dropped),
        }

    def audit_comment(self, provider_name: str, *, category_assigned: bool = False) -> str:
        parts = [
            f"provider={provider_name}",
            f"added={self.added}",
            f"updated={self.updated}",
            f"removed={self.removed}",
            f"archived={self.archived}",
            f"restored={self.restored}",
            f"skipped={self.skipped}",
        ]
        if self.canonical:
            parts.append("canonical=" + _key_list(self.canonical))
        if self.kept_manual:
            parts.append("kept_manual=" + _key_list(self.kept_manual))
        if self.kept_other_provider:
            parts.append("kept_other=" + _key_list(self.kept_other_provider))
        if category_assigned:
            # Field name only, never the category — an audit comment is a
            # summary of what moved, not a copy of it.
            parts.append("category_assigned=1")
        return " ".join(parts)


def apply_provider_category(
    db,
    *,
    ws_id: UUID,
    part,
    provider_name: str,
    provider_category: str | None,
    description: str | None = None,
    user_id: UUID | None = None,
    index: CategoryIndex | None = None,
) -> CategoryOutcome:
    """File an uncategorized part from the provider's own taxonomy.

    Only ever fills a NULL `category_id`. A category the user picked is a
    decision, and a vendor taxonomy is not allowed to overrule it.

    Returns the schema slug to hand `reconcile_provider_specs`, and the
    path we could not resolve so the caller can surface it as a
    suggestion rather than silently doing nothing.

    **The slug is not simply "the part's category".** The category says
    where the part is filed; the slug says which spec schema reads its
    payload, and the two come apart constantly:

    * the sub-category seed (A6) has not run anywhere, so
      "Capacitors / Ceramic" falls back to the "Capacitors" ROOT — and a
      bare "Capacitors" classifies to nothing, because the dielectric
      changes the whole spec set. Taking the slug from the row it landed
      on would leave every capacitor and every transistor in every real
      workspace with the common keys only.
    * a part the user filed under "Bias network" has a category that
      classifies to nothing either, while the provider's taxonomy still
      knows it is a resistor.

    So the provider's path is consulted in both branches, and the part's
    own category only wins when it actually classifies.

    `index` lets a caller in a loop (bulk-import-from-scan, up to 50
    parts) pay for the workspace's category rows once. A caller that
    passes nothing still pays only once: the snapshot below is shared by
    all three lookups this function makes.
    """
    path = category_for_provider(provider_name, provider_category, description)
    provider_slug = category_slug_for(path)
    # Three questions, one tree: the part's own name path, where the
    # provider's path resolves, and the name path of the row we filed it
    # into. Asked separately they were three full scans of
    # `part_categories` on every refresh — the route has no loop to hang a
    # shared snapshot off, so the sharing has to live here. Built lazily,
    # so an uncategorized part whose provider names nothing costs no query
    # at all.
    if index is None and (part.category_id is not None or path is not None):
        index = category_index(db, ws_id=ws_id)

    own_slug = category_slug_for(
        category_name_path(db, ws_id=ws_id, category_id=part.category_id, index=index)
    )

    if part.category_id is not None:
        return CategoryOutcome(
            category_id=part.category_id,
            assigned=False,
            suggestion=None,
            slug=own_slug or provider_slug,
        )
    if path is None:
        return CategoryOutcome(None, False, None, None)

    category = resolve_category_path_or_root(db, ws_id=ws_id, path=path, index=index)
    if category is None:
        # Nothing to file it under. The part keeps a NULL category and the
        # caller reports the path; the specs are still normalised.
        return CategoryOutcome(None, False, path, provider_slug)

    part.category_id = category.id
    part.updated_by = user_id
    filed_slug = category_slug_for(
        category_name_path(db, ws_id=ws_id, category_id=category.id, index=index)
    )
    return CategoryOutcome(
        category_id=category.id,
        assigned=True,
        suggestion=None,
        # The provider's path first: it is the finer of the two whenever
        # they differ, and they differ exactly when the root fallback fired.
        slug=provider_slug or filed_slug,
    )


def reconcile_provider_specs(
    db,
    *,
    ws_id: UUID,
    part,
    provider_name: str,
    raw_specs: Sequence[tuple[str, str]],
    category_slug: str | None,
    is_primary: bool,
    user_id: UUID | None = None,
    description: str | None = None,
    extra_fields: Mapping[str, str] | None = None,
    request_id: str | None = None,
    category_assigned: bool = False,
) -> ReconcileReport:
    """Write one provider payload onto one part, and audit it.

    `raw_specs` is the provider's `(key, value)` list in payload order —
    `[(s["key"], s["value"]) for s in result["specs"]]` at both call
    sites. `extra_fields` carries the non-spec rows the caller resolved
    itself (`image_url`, `datasheet_url`, `source_url`, and a secondary's
    `category`): they are namespaced by the same rule and reconciled in
    the same pass, so the delete pass sees them as present rather than
    stale. `description` is only read for Mouser, whose parametric values
    live in prose.

    Caller owns the transaction. Nothing here commits.
    """
    norm = normalise(
        category_slug, provider_name, list(raw_specs), description=description
    )
    # ARCHIVED rows are loaded too, and that is load-bearing: `uq_cf_unique`
    # (workspace_id, object_type, object_id, key) has no partial WHERE, so a
    # row this reconcile retired last time still occupies its key. Writing
    # past it would be an IntegrityError — a 500 on refresh, and a rolled-back
    # row in the middle of a bulk import. A key that comes back with a real
    # value is un-archived instead (see `_write`).
    rows = _rows_for(db, ws_id=ws_id, part_id=part.id)
    by_key = {row.key: row for row in rows}

    added = updated = restored = 0
    canonical: list[str] = []
    kept_manual: list[str] = []
    kept_other: list[str] = []

    for key, value in norm.canonical.items():
        row = by_key.get(key)
        display = truncate_provider_field_value(value.display)
        if row is None:
            db.add(
                _new_row(
                    ws_id=ws_id,
                    part_id=part.id,
                    key=key,
                    value=display,
                    provider=provider_name,
                    user_id=user_id,
                    value_num=value.value_num,
                )
            )
            added += 1
            canonical.append(key)
            continue
        if row.source != "provider":
            # `manual` and `override` are the user's, and so is anything a
            # future `source` value might mean — the fail-safe branch is the
            # one that changes nothing. An override's remembered upstream
            # value still moves, so a later Restore lands on what the vendor
            # says now; its live value does not.
            if row.source == "override" and provider_outranks(
                provider_name, row.provider
            ):
                if row.original_value != display:
                    row.original_value = display
                    updated += 1
                row.provider = provider_name
                row.updated_by = user_id
                # An override the user made on a row that was later archived
                # is still the user's row; bring it back with the rest.
                restored += _unarchive(row, user_id)
            kept_manual.append(key)
            continue
        # An ARCHIVED provider row is unowned, whoever is stamped on it.
        # Precedence decides who holds a LIVE answer to a key; a retired
        # one holds no answer at all, and `uq_cf_unique` forbids writing a
        # second row beside it — so letting the stamp win here would lock
        # every lower-precedence provider out of that key permanently.
        if row.archived_at is None and not provider_outranks(
            provider_name, row.provider
        ):
            kept_other.append(key)
            continue
        if row.value != display or row.value_num != value.value_num:
            row.value = display
            row.value_num = value.value_num
            row.updated_by = user_id
            updated += 1
        # Claiming provenance is not a change the operator did anything to
        # see, so it is not counted as an update. A resurrection is.
        row.provider = provider_name
        restored += _unarchive(row, user_id)
        canonical.append(key)

    desired, skipped = _namespaced_desired(
        norm, provider_name, is_primary=is_primary, extra_fields=extra_fields
    )
    for key, value in desired.items():
        row = by_key.get(key)
        if row is None:
            db.add(
                _new_row(
                    ws_id=ws_id,
                    part_id=part.id,
                    key=key,
                    value=value,
                    provider=provider_name,
                    user_id=user_id,
                )
            )
            added += 1
        elif row.source == "provider":
            if row.value != value:
                row.value = value
                row.updated_by = user_id
                updated += 1
            # These keys are never parsed, so a number left behind by a
            # previous life of this key (an A5-backfilled row, a key that
            # was canonical under the part's old category) would sort a
            # catalog row against real quantities. Clear it.
            row.value_num = None
            row.provider = provider_name
            restored += _unarchive(row, user_id)
        elif row.source == "override":
            if row.original_value != value:
                row.original_value = value
                row.updated_by = user_id
                updated += 1
            row.provider = provider_name
            restored += _unarchive(row, user_id)
            kept_manual.append(key)
        else:
            # `manual` — the user's row, in this provider's namespace.
            # Reported for the same reason the canonical pass reports one:
            # "we had a value and did not write it" is not silence.
            kept_manual.append(key)

    touched = set(norm.canonical) | set(desired)
    archived, removed = _retire(
        rows,
        provider_name=provider_name,
        is_primary=is_primary,
        touched=touched,
        user_id=user_id,
        db=db,
    )

    report = ReconcileReport(
        added=added,
        updated=updated,
        removed=removed,
        skipped=skipped,
        archived=archived,
        restored=restored,
        canonical=tuple(canonical),
        kept_manual=tuple(kept_manual),
        kept_other_provider=tuple(kept_other),
        dropped=tuple(norm.dropped),
    )
    _audit_log_ids(
        db,
        workspace_id=ws_id,
        user_id=user_id,
        action=AUDIT_ACTION,
        target_type="part",
        target_ids=[part.id],
        comment=report.audit_comment(
            provider_name, category_assigned=category_assigned
        ),
        request_id=request_id,
    )
    return report


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------
def _rows_for(db, *, ws_id: UUID, part_id: UUID) -> list[CustomField]:
    """Every `custom_fields` row on this part, archived included."""
    return list(
        db.execute(
            select(CustomField)
            .where(CustomField.workspace_id == ws_id)
            .where(CustomField.object_type == "part")
            .where(CustomField.object_id == part_id)
        ).scalars()
    )


def _unarchive(row: CustomField, user_id: UUID | None) -> int:
    """A retired key that upstream answers again is live data once more.

    Restoring beats inserting alongside: `uq_cf_unique` would refuse the
    insert, and it keeps one row per key rather than a live one shadowing
    a retired one nothing can reach. Returns 1 when it actually revived a
    row, so the caller can report it — the value may not have moved, but
    the row reappearing on the Specs tab is the change the operator sees.
    """
    if row.archived_at is None:
        return 0
    row.archived_at = None
    row.updated_by = user_id
    return 1


def _new_row(
    *,
    ws_id: UUID,
    part_id: UUID,
    key: str,
    value: str,
    provider: str,
    user_id: UUID | None,
    value_num=None,
) -> CustomField:
    return CustomField(
        workspace_id=ws_id,
        object_type="part",
        object_id=part_id,
        key=key,
        value=value,
        value_num=value_num,
        source="provider",
        provider=provider,
        created_by=user_id,
        updated_by=user_id,
    )


def _namespaced_desired(
    norm,
    provider_name: str,
    *,
    is_primary: bool,
    extra_fields: Mapping[str, str] | None,
) -> tuple[dict[str, str], int]:
    """Catalog + optional + caller-supplied rows, under the ADR-0031 rule.

    A field is SKIPPED, and counted, rather than dropped silently:

    * its key would overflow `custom_fields.key` — on either tier.
      Truncating the KEY would collide two different attributes onto one
      row, so the field goes instead. The prefix is what usually causes
      this, but an upstream name longer than 256 characters does it to
      the primary too.
    * (primary only) the bare key already looks namespaced, which would
      write it outside its own reconcile scope for whichever secondary
      owns that prefix to delete later.
    * (primary only) the bare key spells a canonical one, which
      `uq_cf_unique` has no room for beside the canonical row.
    """
    desired: dict[str, str] = {}
    skipped = 0
    for key, value in list(norm.catalog.items()) + list(norm.optional.items()) + list(
        (extra_fields or {}).items()
    ):
        if is_primary:
            if is_provider_namespaced_key(key):
                skipped += 1
                continue
            # A bare upstream key that spells a CANONICAL one (`package`,
            # `mounting`) cannot be a second row: `uq_cf_unique` allows one
            # row per key, so the insert would be a 500 rather than a
            # duplicate. Tested against the whole schema, not just the keys
            # this payload happened to fill — the part's category decides
            # which keys are canonical, and a payload that answers
            # `Package / Case` for an IC still must not write a bare
            # `package` beside a row some other category's refresh left.
            # A secondary is safe by construction: its keys are prefixed.
            if key in all_canonical_keys():
                skipped += 1
                continue
            stored = key
        else:
            stored = namespaced_custom_field_key(provider_name, key)
            if len(stored) > CUSTOM_FIELD_KEY_MAX:
                skipped += 1
                continue
        if len(stored) > CUSTOM_FIELD_KEY_MAX:
            # The primary writes bare keys, so this is an upstream name
            # longer than the column on its own. Rare, and still not a
            # reason to 500.
            skipped += 1
            continue
        desired[stored] = truncate_provider_field_value(value)
    return desired, skipped


def _retire(
    rows: list[CustomField],
    *,
    provider_name: str,
    is_primary: bool,
    touched: set[str],
    user_id: UUID | None,
    db,
) -> tuple[int, int]:
    """Archive the junk, delete the merely stale. Returns `(archived, removed)`.

    Scoped by `provider_wrote_custom_field_row` — the STRICT test, not
    the one the write pass uses. A write may claim an unstamped canonical
    row, because somebody has to own the 9,377 rows that predate the
    `provider` column. A delete may not: "no one recorded who wrote this"
    is not evidence that I did, and a secondary refresh acting on that
    reading would hard-delete the primary's un-backfilled rows.

    Only `source='provider'` rows are in scope: a `manual` or `override`
    row is the user's, whatever its key says. Already-archived rows are
    skipped outright, so a row retired once is never hard-deleted later
    and never counted as newly archived on the next refresh.

    Junk is archived rather than deleted because it is the one class of
    row this change removes from parts that have carried it for months —
    277 ECCN rows, ~1,000 `-` values — and an archived row can be read
    back and counted. A row the provider merely stopped sending keeps the
    hard delete it has always had. `value_num` goes with it: the partial
    index on that column exists to sort live specs, and a retired row has
    no business in it.
    """
    archived = removed = 0
    for row in rows:
        if row.source != "provider" or row.key in touched:
            continue
        if row.archived_at is not None:
            # Already retired by an earlier pass. Leave it archived rather
            # than hard-deleting it now — the record is the point.
            continue
        if not provider_wrote_custom_field_row(
            provider_name, row, is_primary=is_primary
        ):
            continue
        if is_junk_key(_bare_key(row.key, provider_name)) or is_junk_value(row.value):
            row.archived_at = utcnow()
            row.value_num = None
            row.updated_by = user_id
            archived += 1
        else:
            db.delete(row)
            removed += 1
    return archived, removed


def _bare_key(key: str, provider_name: str) -> str:
    prefix = f"{provider_name}:"
    return key[len(prefix):] if key.startswith(prefix) else key


def _key_list(keys: Sequence[str]) -> str:
    shown = sorted(keys)
    if len(shown) <= _AUDIT_KEY_LIMIT:
        return ",".join(shown)
    return ",".join(shown[:_AUDIT_KEY_LIMIT]) + f",+{len(shown) - _AUDIT_KEY_LIMIT}"
