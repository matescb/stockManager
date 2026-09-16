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
    category_name_path,
    resolve_category_path_or_root,
)
from app.domain.custom_fields.models import CustomField
from app.domain.parts.provider_fields import (
    CUSTOM_FIELD_KEY_MAX,
    is_provider_namespaced_key,
    namespaced_custom_field_key,
    provider_owns_custom_field_row,
)
from app.domain.parts.services.provider_field_values import (
    truncate_provider_field_value,
)
from app.domain.parts.spec_schema import (
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
    #: Fields whose namespaced key would not fit `custom_fields.key`.
    skipped: int = 0
    #: Junk keys and `-` values retired from a part that already had them.
    archived: int = 0
    #: Canonical keys this provider now owns on the part.
    canonical: tuple[str, ...] = ()
    #: Canonical keys left to a `manual` / `override` row.
    kept_manual: tuple[str, ...] = ()
    #: Canonical keys a higher-precedence provider already answered.
    kept_other_provider: tuple[str, ...] = ()
    #: Raw payload keys refused: junk, placeholder values, losing aliases.
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
        }

    def audit_comment(self, provider_name: str) -> str:
        parts = [
            f"provider={provider_name}",
            f"added={self.added}",
            f"updated={self.updated}",
            f"removed={self.removed}",
            f"archived={self.archived}",
            f"skipped={self.skipped}",
        ]
        if self.canonical:
            parts.append("canonical=" + _key_list(self.canonical))
        if self.kept_manual:
            parts.append("kept_manual=" + _key_list(self.kept_manual))
        if self.kept_other_provider:
            parts.append("kept_other=" + _key_list(self.kept_other_provider))
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
) -> CategoryOutcome:
    """File an uncategorized part from the provider's own taxonomy.

    Only ever fills a NULL `category_id`. A category the user picked is a
    decision, and a vendor taxonomy is not allowed to overrule it — which
    is also why the part keeps its own category's schema when it has one.

    Returns the schema slug to hand `reconcile_provider_specs`, and the
    path we could not resolve so the caller can surface it as a
    suggestion rather than silently doing nothing.
    """
    if part.category_id is not None:
        return CategoryOutcome(
            category_id=part.category_id,
            assigned=False,
            suggestion=None,
            slug=category_slug_for(
                category_name_path(db, ws_id=ws_id, category_id=part.category_id)
            ),
        )

    path = category_for_provider(provider_name, provider_category, description)
    if path is None:
        return CategoryOutcome(None, False, None, None)

    category = resolve_category_path_or_root(db, ws_id=ws_id, path=path)
    if category is None:
        # Nothing to file it under. The part keeps a NULL category and the
        # caller reports the path; the schema slug still comes from the
        # path, so the specs are normalised even though the tree has no
        # home for the part yet.
        return CategoryOutcome(None, False, path, category_slug_for(path))

    part.category_id = category.id
    part.updated_by = user_id
    return CategoryOutcome(
        category_id=category.id,
        assigned=True,
        suggestion=None,
        slug=category_slug_for(
            category_name_path(db, ws_id=ws_id, category_id=category.id)
        ),
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

    added = updated = 0
    canonical: list[str] = []
    kept_manual: list[str] = []
    kept_other: list[str] = []

    for key, value in norm.canonical.items():
        row = by_key.get(key)
        if row is None:
            db.add(
                _new_row(
                    ws_id=ws_id,
                    part_id=part.id,
                    key=key,
                    value=truncate_provider_field_value(value.display),
                    provider=provider_name,
                    user_id=user_id,
                    value_num=value.value_num,
                )
            )
            added += 1
            canonical.append(key)
        elif row.source != "provider":
            # `manual` and `override` are the user's, and so is anything a
            # future `source` value might mean — the fail-safe branch is the
            # one that changes nothing. An override's remembered upstream
            # value still moves, so a later Restore lands on what the vendor
            # says now; its live value does not.
            if row.source == "override" and provider_outranks(
                provider_name, row.provider
            ):
                row.original_value = truncate_provider_field_value(value.display)
                row.provider = provider_name
                row.updated_by = user_id
            kept_manual.append(key)
        elif not provider_outranks(provider_name, row.provider):
            kept_other.append(key)
        else:
            display = truncate_provider_field_value(value.display)
            if row.value != display or row.value_num != value.value_num:
                row.value = display
                row.value_num = value.value_num
                row.updated_by = user_id
                updated += 1
            # Claiming provenance is not a change the operator did
            # anything to see, so it is not counted as an update.
            row.provider = provider_name
            _unarchive(row, user_id)
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
            row.provider = provider_name
            _unarchive(row, user_id)
        elif row.source == "override":
            if row.original_value != value:
                row.original_value = value
                row.updated_by = user_id

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
        comment=report.audit_comment(provider_name),
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


def _unarchive(row: CustomField, user_id: UUID | None) -> None:
    """A retired key that upstream answers again is live data once more.

    Restoring beats inserting alongside: `uq_cf_unique` would refuse the
    insert, and it keeps one row per key rather than a live one shadowing
    a retired one nothing can reach.
    """
    if row.archived_at is not None:
        row.archived_at = None
        row.updated_by = user_id


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

    Unchanged behaviour on both tiers. A secondary SKIPS a field whose
    namespaced key would overflow `custom_fields.key` rather than
    truncating the key and colliding two attributes onto one row; the
    primary skips a bare key that already looks namespaced, which would
    otherwise be written outside its own reconcile scope and later
    deleted by whichever secondary owns that prefix.
    """
    desired: dict[str, str] = {}
    skipped = 0
    for key, value in list(norm.catalog.items()) + list(norm.optional.items()) + list(
        (extra_fields or {}).items()
    ):
        if is_primary:
            if is_provider_namespaced_key(key):
                continue
            # A bare upstream key that happens to spell a canonical one
            # (`package`, `mounting`) would be a SECOND row for a key the
            # canonical pass just wrote — impossible under `uq_cf_unique`,
            # so the insert would be a 500 rather than a duplicate. The
            # canonical row is the better answer anyway; drop the other.
            # A secondary is safe by construction: its keys are prefixed.
            if key in norm.canonical:
                continue
            stored = key
        else:
            stored = namespaced_custom_field_key(provider_name, key)
            if len(stored) > CUSTOM_FIELD_KEY_MAX:
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

    Only rows this provider owns are in scope
    (`provider_owns_custom_field_row`), and only `source='provider'` ones:
    a `manual` or `override` row is the user's, whatever its key says.

    Junk is archived rather than deleted because it is the one class of
    row this change removes from parts that have carried it for months —
    277 ECCN rows, ~1,000 `-` values — and an archived row can be read
    back and counted. A row the provider merely stopped sending keeps the
    hard delete it has always had.
    """
    archived = removed = 0
    for row in rows:
        if row.source != "provider" or row.key in touched:
            continue
        if row.archived_at is not None:
            # Already retired by an earlier pass. Leave it archived rather
            # than hard-deleting it now — the record is the point.
            continue
        if not provider_owns_custom_field_row(
            provider_name, row, is_primary=is_primary
        ):
            continue
        if is_junk_key(_bare_key(row.key, provider_name)) or is_junk_value(row.value):
            row.archived_at = utcnow()
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
