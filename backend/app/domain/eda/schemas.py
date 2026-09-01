"""Pydantic shapes for the EDA API.

Every input schema keeps `model_config = ConfigDict(extra="forbid")` —
pinned by `tests/test_eda.py::test_patch_rejects_unknown_field` and
`::test_part_eda_rejects_unknown_field` (`tests/test_extra_forbid.py`
is a hand-maintained list that does NOT cover this router).

Upload metadata (`name`, `category_id`) arrives as multipart form
fields rather than through a body schema, so those routes validate
their own two parameters inline; everything else goes through a model
here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "EDA_NAME_MAX",
    "EdaSymbolPatch",
    "EdaSymbolOut",
    "EdaFootprintPatch",
    "EdaFootprintOut",
    "EdaDatafilePatch",
    "EdaDatafileOut",
    "EdaFootprintModelIn",
    "EdaFootprintModelOut",
    "PartEdaIn",
    "PartEdaOut",
]

# Matches `String(200)` on the `name` columns — the KiCad entry name.
EDA_NAME_MAX = 200

EdaName = Annotated[str, Field(min_length=1, max_length=EDA_NAME_MAX)]

# A KiCad footprint-chooser filter glob, e.g. "R_0402_*". Same shape the
# categories schema uses, and the two are merged at serve time in phase 5.
FootprintFilter = Annotated[str, Field(min_length=1, max_length=100)]


class EdaSymbolPatch(BaseModel):
    """Rename / re-file a hosted symbol.

    The file itself is immutable — it's content-addressed, so "editing"
    one means uploading the new bytes. Only the metadata moves.
    """

    model_config = ConfigDict(extra="forbid")

    name: EdaName | None = None
    category_id: UUID | None = None


class EdaSymbolOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    sha256: str
    size_bytes: int
    source: str
    category_id: UUID | None
    archived_at: datetime | None


class EdaFootprintPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: EdaName | None = None
    category_id: UUID | None = None


class EdaFootprintOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    sha256: str
    size_bytes: int
    source: str
    category_id: UUID | None
    archived_at: datetime | None


class EdaDatafilePatch(BaseModel):
    """Rename a data file. `kind` is derived from the uploaded filename
    and never changes — a STEP file cannot become a SPICE model."""

    model_config = ConfigDict(extra="forbid")

    name: EdaName | None = None


class EdaDatafileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    name: str
    sha256: str
    size_bytes: int
    source: str
    archived_at: datetime | None


class EdaFootprintModelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datafile_id: UUID
    # Bounded so an out-of-range int can't blow past Postgres INTEGER and
    # surface as a 500 (house convention: every int input carries bounds).
    position: int = Field(default=0, ge=0, le=1_000_000)


class EdaFootprintModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    datafile_id: UUID
    position: int


class PartEdaIn(BaseModel):
    """The body of `PUT /api/parts/{part_id}/eda`.

    A full replacement, not a merge: every field is optional, and one
    the caller omits is written as its default (null, or false/true for
    the exclude flags). That is what PUT means, it's what the CAD tab
    sends — the whole form on every save — and it's the only reading
    under which "clear the symbol" is expressible at all.

    `symbol_id` and `symbol_ref_external` are mutually exclusive (as are
    the footprint pair); sending both is a 422 `eda.ref_conflict` rather
    than a silent precedence rule. See `PartEda`'s docstring for what
    the two mean.
    """

    model_config = ConfigDict(extra="forbid")

    symbol_id: UUID | None = None
    symbol_ref_external: str | None = Field(default=None, max_length=200)
    footprint_id: UUID | None = None
    footprint_ref_external: str | None = Field(default=None, max_length=200)
    spice_datafile_id: UUID | None = None

    value: str | None = Field(default=None, max_length=120)
    keywords: str | None = Field(default=None, max_length=300)
    # Capped so one row can't push an unbounded array into Postgres.
    footprint_filters: list[FootprintFilter] | None = Field(default=None, max_length=50)

    exclude_from_bom: bool = False
    exclude_from_board: bool = False
    exclude_from_sim: bool = True

    sim_device: str | None = Field(default=None, max_length=60)
    sim_pins: str | None = Field(default=None, max_length=300)
    sim_params: str | None = Field(default=None, max_length=500)


class PartEdaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    part_id: UUID
    symbol_id: UUID | None
    symbol_ref_external: str | None
    footprint_id: UUID | None
    footprint_ref_external: str | None
    spice_datafile_id: UUID | None
    value: str | None
    keywords: str | None
    footprint_filters: list[str] | None
    exclude_from_bom: bool
    exclude_from_board: bool
    exclude_from_sim: bool
    sim_device: str | None
    sim_pins: str | None
    sim_params: str | None
