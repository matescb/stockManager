from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=300)
    description: str | None = None
    notes_markdown: str | None = None
    associated_subassembly_part_id: UUID | None = None


class ProjectPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    notes_markdown: str | None = None
    associated_subassembly_part_id: UUID | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    notes_markdown: str | None
    archived_at: str | None = None


# BOM
class BomEntryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_type: Literal["part", "meta_part", "non_part", "unmatched"] = "part"
    part_id: UUID | None = None
    meta_part_id: UUID | None = None
    name: str | None = None
    # DB-005 / migration 0031 — integer quantities only (no fractional BOM rows).
    quantity: int = Field(default=1, ge=0)
    comments: str | None = None
    designators: list[str] = []
    cad_footprint: str | None = None
    cad_key: str | None = None
    dnp: bool = False


class BomEntryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_type: Literal["part", "meta_part", "non_part", "unmatched"] | None = None
    part_id: UUID | None = None
    meta_part_id: UUID | None = None
    name: str | None = None
    # DB-005 / migration 0031 — integer quantities only.
    quantity: int | None = Field(default=None, ge=0)
    comments: str | None = None
    designators: list[str] | None = None
    cad_footprint: str | None = None
    cad_key: str | None = None
    dnp: bool | None = None


class BomImportPreviewIn(BaseModel):
    """Step 1: parse the upload, return preview rows + suggested separator."""
    model_config = ConfigDict(extra="forbid")

    # Cap base64 input at 6 MB (≈4.5 MB raw after decode). The importer
    # asserts the decoded size separately at 4 MB so a payload that
    # squeaks under this Field limit but decodes past the runtime cap
    # still trips the post-decode 413 guard. The 6 MB / 4 MB pairing
    # is deliberate: Field validation must allow a payload large enough
    # for the runtime check to fire, otherwise the layered defence
    # collapses to just the Pydantic 422. SEC2-007 / BE2-006.
    text_b64: str = Field(..., max_length=6_000_000)
    separator: str | None = None  # auto-detect if None
    encoding: str | None = None
    has_header: bool | None = None


class BomMappingField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column_index: int
    target: Literal[
        "ignore",
        "quantity",
        "part",
        "mpn",
        "manufacturer",
        "internal_part_number",
        "designators",
        "comments",
        "footprint",
        "id_code",
        "cad_key",
        "dnp",
    ]


class BomImportCommitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # See BomImportPreviewIn.text_b64 — same cap; SEC2-007 / BE2-006.
    text_b64: str = Field(..., max_length=6_000_000)
    separator: str
    encoding: str
    has_header: bool
    mapping: list[BomMappingField]
    designator_separator: str = ","


class BomPreviewRow(BaseModel):
    cells: list[str]


class BomImportPreviewOut(BaseModel):
    detected_separator: str
    detected_encoding: str
    has_header: bool
    headers: list[str] | None
    rows: list[BomPreviewRow]


class BomImportCommitOut(BaseModel):
    inserted: int
    matched: int
    unmatched: int
