"""Validating and applying one batch of part specifications.

Split out of `write_parts.py` because `set_part_specs` is the only tool
in this codebase that takes a whole dict of user data in one call, and
the rules that follow from that — a key cap, a length cap, all-or-nothing
validation, and a per-row ownership check — are a different job from
being an MCP tool.

The ownership rule is the one to read before changing anything here.
A `custom_fields` row's `source` says who may write it:

* `provider` — a parts-provider refresh owns it. Skipped, always,
  including by `replace_missing`. ADR-0031 is the namespace contract
  behind that, and the refresh's "delete rows absent from my payload"
  pass is what makes silently borrowing one dangerous.
* `manual` / `override` — a person (or this tool) owns it. Written in
  place. Nothing here changes a row's `source`, so provenance is only
  ever set by the path that earned it: a `manual` row stays manual, and
  an `override` a person made in the UI stays an override.

Writing and deleting are not the same permission. `replace_missing`
removes only `manual` rows; an override is updatable but never
deletable, because deleting it would also throw away the
`original_value` that is the sole copy of what the provider said. See
`_remove_absent`.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import status
from sqlalchemy import select

from app.core.errors import ErrorCodes, raise_http
from app.domain.custom_fields.models import CustomField
from app.domain.parts.provider_fields import (
    CUSTOM_FIELD_KEY_MAX,
    is_provider_namespaced_key,
    is_provider_reserved_custom_field_key,
)
from app.mcp.principal import Caller

__all__ = [
    "MAX_SPEC_KEYS",
    "CUSTOM_FIELD_VALUE_MAX",
    "SpecWrite",
    "apply_specs",
    "keys_comment",
    "load_part_fields",
    "validate_specs",
]

# Width of `custom_fields.value` (see `domain/custom_fields/models.py`).
CUSTOM_FIELD_VALUE_MAX = 1024

# Most keys one call may write. A part with fifty specifications is
# already past what any schematic needs, and an unbounded dict is an
# unbounded number of rows behind a single rate-limit token.
MAX_SPEC_KEYS = 50

# Ceiling on the audit comment's key list, so one bulk call cannot write
# a 12 kB audit row. The count survives the truncation, which is the part
# an auditor actually needs.
_AUDIT_KEYS_MAX = 400

# The row sources this tool may write; see the module docstring.
_WRITABLE_SOURCES = ("manual", "override")


class SpecWrite:
    """What one batch did, per key. Every list is sorted for a stable answer."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.updated: list[str] = []
        self.unchanged: list[str] = []
        self.skipped_provider_owned: list[str] = []
        self.removed: list[str] = []

    @property
    def touched(self) -> list[str]:
        """The keys that actually changed — what the audit row names."""
        return sorted(self.created + self.updated + self.removed)

    def as_payload(self) -> dict[str, list[str]]:
        return {
            "created": sorted(self.created),
            "updated": sorted(self.updated),
            "unchanged": sorted(self.unchanged),
            "skipped_provider_owned": sorted(self.skipped_provider_owned),
            "removed": sorted(self.removed),
        }


def load_part_fields(caller: Caller, part_id: UUID) -> dict[str, CustomField]:
    """Every custom-field row on this part, keyed by key.

    One query rather than one per key: a fifty-key batch would otherwise
    be fifty round trips before the first write.

    ARCHIVED rows are included, and must stay included: `uq_cf_unique` has
    no partial WHERE, so a row the spec reconcile retired still owns its
    key and an insert past it would be an IntegrityError. `apply_specs`
    restores such a row instead (ADR-0034).
    """
    return {
        row.key: row
        for row in caller.db.execute(
            select(CustomField)
            .where(CustomField.workspace_id == caller.ws.id)
            .where(CustomField.object_type == "part")
            .where(CustomField.object_id == part_id)
        ).scalars()
    }


def validate_specs(specs: dict[str, str]) -> None:
    """Refuse the whole batch rather than writing part of it.

    All-or-nothing because the alternative is a model that has to
    reconcile which of fifty keys landed — and the failure modes here
    (a reserved key, an over-long value) are mistakes in the call, not
    facts about the part.
    """
    if len(specs) > MAX_SPEC_KEYS:
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            ErrorCodes.CUSTOM_FIELD_TOO_MANY,
            f"{len(specs)} keys exceeds the {MAX_SPEC_KEYS}-key limit for one "
            "call; split it",
        )
    for key, value in specs.items():
        if not key.strip():
            raise_http(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                ErrorCodes.CUSTOM_FIELD_KEY_WHITESPACE,
                "a specification key cannot be blank",
            )
        # Refused rather than silently stripped, and this is the check
        # that makes the two below sound. Both compare the key EXACTLY,
        # so `"image_url "` is not reserved and `"mouser: x"` is not
        # namespaced — a single space walked past either one. Stripping
        # instead would have to answer what happens when the stripped
        # form collides with another key in the same payload; refusing
        # says which key is wrong and needs no such answer.
        if key != key.strip():
            raise_http(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                ErrorCodes.CUSTOM_FIELD_KEY_WHITESPACE,
                f"key {key!r} has leading or trailing whitespace; send "
                f"{key.strip()!r}",
            )
        if is_provider_reserved_custom_field_key(key) or is_provider_namespaced_key(key):
            raise_http(
                status.HTTP_400_BAD_REQUEST,
                ErrorCodes.CUSTOM_FIELD_RESERVED_KEY,
                f"{key!r} is reserved for provider-managed data and cannot be "
                "written here",
            )
        if len(key) > CUSTOM_FIELD_KEY_MAX:
            raise_http(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                ErrorCodes.CUSTOM_FIELD_TOO_LONG,
                f"key {key[:40]!r}… exceeds {CUSTOM_FIELD_KEY_MAX} characters",
            )
        if len(value) > CUSTOM_FIELD_VALUE_MAX:
            raise_http(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                ErrorCodes.CUSTOM_FIELD_TOO_LONG,
                f"the value for {key!r} exceeds {CUSTOM_FIELD_VALUE_MAX} characters",
            )


def apply_specs(
    caller: Caller,
    part_id: UUID,
    specs: dict[str, str],
    existing: dict[str, CustomField],
    *,
    replace_missing: bool,
) -> SpecWrite:
    """Write `specs` onto the part, and report what each key did."""
    written = SpecWrite()

    for key, value in specs.items():
        row = existing.get(key)
        if row is None:
            caller.db.add(_new_field(caller, part_id, key, value))
            written.created.append(key)
        elif row.source not in _WRITABLE_SOURCES:
            written.skipped_provider_owned.append(key)
        elif row.value == value and row.archived_at is None:
            written.unchanged.append(key)
        else:
            row.value = value
            # A retired key the caller writes again is live data once more.
            # Without this the write lands on a row no reader returns, and
            # the tool reports success for something nobody can see.
            row.archived_at = None
            row.updated_by = caller.user.id
            written.updated.append(key)

    if replace_missing:
        written.removed = _remove_absent(caller, specs, existing)
    return written


def _new_field(caller: Caller, part_id: UUID, key: str, value: str) -> CustomField:
    return CustomField(
        workspace_id=caller.ws.id,
        object_type="part",
        object_id=part_id,
        key=key,
        value=value,
        source="manual",
        created_by=caller.user.id,
        updated_by=caller.user.id,
    )


def _remove_absent(
    caller: Caller, specs: dict[str, str], existing: dict[str, CustomField]
) -> list[str]:
    """Delete the plain `manual` rows the new payload does not name.

    NARROWER than the set `apply_specs` may write, and deliberately so.
    Updating an `override` changes a value; deleting one destroys a
    person's decision — the row is the record that somebody looked at a
    provider value and replaced it, and `original_value` is the only
    copy of what upstream said. Throw both away and the next provider
    refresh restores the upstream value as though the disagreement had
    never happened. So an override is updatable and never deletable, and
    `replace_missing` means "replace what I wrote", not "replace
    everything I am allowed to touch".

    Provider rows and the reserved / namespaced keys are not this tool's
    at all, so `replace_missing` can never strip a part's provider data.
    """
    removed: list[str] = []
    for key, row in existing.items():
        if key in specs or row.source != "manual":
            continue
        if is_provider_reserved_custom_field_key(key) or is_provider_namespaced_key(key):
            continue
        caller.db.delete(row)
        removed.append(key)
    return removed


def keys_comment(keys: list[str]) -> str:
    """`keys=a,b,c` — names only, never values.

    A specification value can be anything a vendor page contained;
    `audit_log.comment` is a low-sensitivity summary (CLAUDE.md), and
    which fields moved is the part an auditor needs.
    """
    rendered = ",".join(keys)
    if len(rendered) <= _AUDIT_KEYS_MAX:
        return f"keys={rendered}"
    cut = rendered[:_AUDIT_KEYS_MAX].rsplit(",", 1)[0]
    shown = cut.count(",") + 1
    return f"keys={cut} (+{len(keys) - shown} more)"
