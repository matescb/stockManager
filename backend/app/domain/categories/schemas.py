"""Pydantic shapes for the part-categories API.

Every input schema keeps `model_config = ConfigDict(extra="forbid")` —
guarded by `tests/test_categories.py::test_create_rejects_unknown_field`
and `::test_patch_rejects_unknown_field` (`tests/test_extra_forbid.py` is
a hand-maintained list that does NOT cover this router).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "PLACEHOLDER_PATTERN",
    "PartCategoryIn",
    "PartCategoryPatch",
    "PartCategoryOut",
    "LIBRARY_SLUG_PATTERN",
    "MAX_KICAD_FIELDS",
    "SPEC_KEY_PATTERN",
]

# Lower-case alphanumerics joined by single dashes. Same shape the
# server derives from `name`, so a hand-written slug and a derived one
# are indistinguishable downstream (KiCad library nicknames, URLs).
LIBRARY_SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"

# A KiCad footprint-chooser filter glob, e.g. "R_0402_*".
FootprintFilter = Annotated[str, Field(min_length=1, max_length=100)]

LibrarySlug = Annotated[
    str,
    Field(min_length=1, max_length=60, pattern=LIBRARY_SLUG_PATTERN),
]

# A `value_template` placeholder. A COPY of
# `domain/eda/value_template.py::PLACEHOLDER_PATTERN`, duplicated rather
# than imported so this module — which `domain/eda` imports — does not
# import `domain/eda` back. Fourteen characters is a cheaper coupling
# than a cycle waiting to happen;
# `tests/test_value_template.py::test_the_two_placeholder_patterns_agree`
# fails if they drift.
PLACEHOLDER_PATTERN = re.compile(r"\{([a-z_]+)\}")

# A canonical spec key — the same shape a placeholder accepts inside
# braces, so a key that is legal in `kicad_fields` is legal in a template
# and vice versa.
SPEC_KEY_PATTERN = r"^[a-z_]+$"

# Caps `kicad_fields`, and the number of placeholders one template may
# carry. Twenty hidden fields is already more than any symbol properties
# dialog is readable with; the cap is here so one row cannot push an
# unbounded list into Postgres or an unbounded field set at KiCad.
MAX_KICAD_FIELDS = 20

SpecKey = Annotated[str, Field(min_length=1, max_length=64, pattern=SPEC_KEY_PATTERN)]


def _validated_template(value: str) -> str:
    """Refuse a template whose braces aren't placeholders.

    Rendering leaves unrecognised braces as literal text rather than
    raising (see `value_template.py`), which is the right runtime
    behaviour and the wrong thing to accept silently from a form: a user
    who typed `{Resistance}` or `{ resistance }` meant a placeholder and
    would otherwise get that text drawn on every symbol in the category
    with no indication of why.
    """
    residue = PLACEHOLDER_PATTERN.sub("", value)
    if "{" in residue or "}" in residue:
        raise ValueError(
            "placeholders are lower-case keys in braces, e.g. "
            "'{resistance} {tolerance} {package}'"
        )
    count = len(PLACEHOLDER_PATTERN.findall(value))
    if count > MAX_KICAD_FIELDS:
        raise ValueError(f"at most {MAX_KICAD_FIELDS} placeholders, got {count}")
    return value


def _deduped(value: list[str]) -> list[str]:
    """Drop repeats, keep the caller's order. A key listed twice would
    emit one field either way — the dict is keyed by name — so storing
    it twice only makes the stored list disagree with the output."""
    return list(dict.fromkeys(value))


ValueTemplate = Annotated[
    str,
    Field(min_length=1, max_length=200),
    AfterValidator(_validated_template),
]

KicadFields = Annotated[
    list[SpecKey],
    Field(max_length=MAX_KICAD_FIELDS),
    AfterValidator(_deduped),
]


class PartCategoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    # Bounded so an out-of-range int can't blow past Postgres INTEGER and
    # surface as a 500 (house convention: every int input carries bounds).
    sort_order: int = Field(default=0, ge=0, le=1_000_000)
    refdes_prefix: str | None = Field(default=None, max_length=10)
    default_symbol_ref: str | None = Field(default=None, max_length=200)
    default_footprint_ref: str | None = Field(default=None, max_length=200)
    # Capped so one row can't push an unbounded array into Postgres.
    footprint_filters: list[FootprintFilter] | None = Field(default=None, max_length=50)
    # What a part in this category shows as its schematic `Value`,
    # rendered from the part's canonical specs — see
    # `domain/eda/value_template.py`. Null inherits from the nearest
    # ancestor category that has one.
    value_template: ValueTemplate | None = None
    # Canonical spec keys emitted as hidden KiCad symbol fields. Null
    # inherits; an empty list means "emit none" and stops the walk.
    kicad_fields: KicadFields | None = None
    # Omit to have the server derive it from `name`.
    library_slug: LibrarySlug | None = None
    # Omit or null to create a root category. Validated by
    # `categories/tree.py::validate_parent` — 404 outside the workspace,
    # 409 if archived, 422 past the depth cap.
    parent_id: UUID | None = None


class PartCategoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    sort_order: int | None = Field(default=None, ge=0, le=1_000_000)
    refdes_prefix: str | None = Field(default=None, max_length=10)
    default_symbol_ref: str | None = Field(default=None, max_length=200)
    default_footprint_ref: str | None = Field(default=None, max_length=200)
    footprint_filters: list[FootprintFilter] | None = Field(default=None, max_length=50)
    # Both nullable, and an explicit null is honoured: it clears the
    # override and hands the category back to whatever its ancestors say.
    value_template: ValueTemplate | None = None
    kicad_fields: KicadFields | None = None
    library_slug: LibrarySlug | None = None
    # Unlike the NOT NULL fields in `_NON_NULLABLE_PATCH_FIELDS`, an
    # explicit `null` here is meaningful and honoured: it moves the
    # category back up to the root of the tree.
    parent_id: UUID | None = None


class PartCategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    sort_order: int
    refdes_prefix: str | None
    default_symbol_ref: str | None
    default_footprint_ref: str | None
    footprint_filters: list[str] | None
    value_template: str | None
    kicad_fields: list[str] | None
    library_slug: str
    parent_id: UUID | None
    archived_at: datetime | None

    @field_validator("kicad_fields", mode="before")
    @classmethod
    def _tolerate_malformed_kicad_fields(cls, value: Any) -> Any:
        """Read a JSONB column that isn't a list of strings as unset.

        Nothing this API accepts can write one — but the column is JSONB
        and a raw-SQL fix or a restored backup can, and
        `domain/eda/kicad_specs.py::_field_list` already survives it.
        Without the same tolerance here the row is a `ValidationError`,
        i.e. a 500 on `GET /api/categories` and a blank settings page,
        for exactly the data the read path was hardened against.
        """
        if value is None or (
            isinstance(value, list) and all(isinstance(item, str) for item in value)
        ):
            return value
        return None
