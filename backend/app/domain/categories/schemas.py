"""Pydantic shapes for the part-categories API.

Every input schema keeps `model_config = ConfigDict(extra="forbid")` —
guarded by `tests/test_categories.py::test_create_rejects_unknown_field`
and `::test_patch_rejects_unknown_field` (`tests/test_extra_forbid.py` is
a hand-maintained list that does NOT cover this router).
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "PartCategoryIn",
    "PartCategoryPatch",
    "PartCategoryOut",
    "LIBRARY_SLUG_PATTERN",
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
    # Omit to have the server derive it from `name`.
    library_slug: LibrarySlug | None = None


class PartCategoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    sort_order: int | None = Field(default=None, ge=0, le=1_000_000)
    refdes_prefix: str | None = Field(default=None, max_length=10)
    default_symbol_ref: str | None = Field(default=None, max_length=200)
    default_footprint_ref: str | None = Field(default=None, max_length=200)
    footprint_filters: list[FootprintFilter] | None = Field(default=None, max_length=50)
    library_slug: LibrarySlug | None = None


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
    library_slug: str
    archived_at: datetime | None
