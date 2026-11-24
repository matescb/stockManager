from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    # DB-005 / migration 0032 — integer quantities only (no fractional BOM rows).
    quantity: int = Field(default=1, ge=0)
    # Track B1 / migration 0072 — per-BOM-line waste rate. 0 <= pct < 100;
    # inflates required + consumed qty (builds/service.py::_required).
    attrition_pct: float = Field(default=0, ge=0, lt=100)
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
    # DB-005 / migration 0032 — integer quantities only.
    quantity: int | None = Field(default=None, ge=0)
    # Track B1 / migration 0072 — per-BOM-line waste rate. 0 <= pct < 100.
    attrition_pct: float | None = Field(default=None, ge=0, lt=100)
    comments: str | None = None
    designators: list[str] | None = None
    cad_footprint: str | None = None
    cad_key: str | None = None
    dnp: bool | None = None


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
    auto_create_missing_parts: bool = Field(default=False)
    mapping: list[BomMappingField] | None = None
    designator_separator: str = ","


class BomImportCommitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # See BomImportPreviewIn.text_b64 — same cap; SEC2-007 / BE2-006.
    text_b64: str = Field(..., max_length=6_000_000)
    separator: str
    encoding: str
    has_header: bool
    mapping: list[BomMappingField]
    designator_separator: str = ","
    auto_create_missing_parts: bool = Field(default=False)


class BomPreviewRow(BaseModel):
    cells: list[str]


class BomImportPreviewOut(BaseModel):
    detected_separator: str
    detected_encoding: str
    has_header: bool
    headers: list[str] | None
    rows: list[BomPreviewRow]
    would_auto_create_count: int = 0
    would_skip_count: int = 0


class BomImportCommitOut(BaseModel):
    inserted: int
    matched: int
    unmatched: int
    auto_created: int = 0
    skipped: int = 0


# BOM sync (SM-011) — `domain/projects/bom_sync.py`. Same caps as the CSV
# importer: 10 000 rows per call (`bom_import._MAX_ROW_COUNT`).
BOM_SYNC_MAX_ROWS = 10_000

# KiCad's BOM export collapses consecutive references into a range
# (`C6-C8`, `--ref-range-delimiter`, default "-"). Expanded so a line's
# identity does not depend on the export setting. Same prefix on both
# ends, ascending, and capped so a typo cannot mint a million designators.
_REF_RANGE_RE = re.compile(r"^([A-Za-z_#]+)(\d+)-\1(\d+)$")
_REF_RANGE_MAX = 1000


def _expand_ref_range(ref: str) -> list[str]:
    match = _REF_RANGE_RE.match(ref)
    if not match:
        return [ref]
    prefix, start, end = match.group(1), int(match.group(2)), int(match.group(3))
    if not start < end or end - start >= _REF_RANGE_MAX:
        return [ref]
    return [f"{prefix}{n}" for n in range(start, end + 1)]


class BomSyncRowIn(BaseModel):
    """One BOM line as an EDA export carries it.

    The field set is `bom_import.ParsedRow`'s, named the way a KiCad /
    netsch export names them: `value` and `name` both feed the line's
    name (`name` wins), `ipn` is the internal part number, `id_code` is a
    bare part uuid or the `StockManager` part URL. A field left out
    (null) leaves the existing line's value alone; see `bom_sync.py`.
    """

    model_config = ConfigDict(extra="forbid")

    designators: list[str] = Field(default_factory=list, max_length=2000)
    quantity: int | None = Field(default=None, ge=0)
    mpn: str | None = Field(default=None, max_length=200)
    manufacturer: str | None = Field(default=None, max_length=200)
    value: str | None = Field(default=None, max_length=300)
    name: str | None = Field(default=None, max_length=300)
    footprint: str | None = Field(default=None, max_length=120)
    id_code: str | None = Field(default=None, max_length=500)
    ipn: str | None = Field(default=None, max_length=200)
    cad_key: str | None = Field(default=None, max_length=300)
    dnp: bool | None = None
    comments: str | None = Field(default=None, max_length=5000)

    @field_validator("designators", mode="before")
    @classmethod
    def _split_designators(cls, value: object) -> object:
        # KiCad's CSV export joins references with ","; netsch splits on
        # whitespace. Accept a string or a list, splitting either on both,
        # and expand KiCad reference ranges.
        items = [value] if isinstance(value, str) else value
        if not isinstance(items, list):
            return value
        out: list[object] = []
        for item in items:
            if not isinstance(item, str):
                out.append(item)
                continue
            for ref in item.replace(",", " ").split():
                out.extend(_expand_ref_range(ref))
        return out


class BomSyncIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[BomSyncRowIn] = Field(max_length=BOM_SYNC_MAX_ROWS)
    dry_run: bool = False


class BomProviderImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_ids: list[UUID] | None = Field(default=None, max_length=50)


class BomProviderImportChoiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    choices: dict[UUID, str] = Field(min_length=1, max_length=50)


class BomProviderCandidate(BaseModel):
    manufacturer: str
    mpn: str | None = None
    description: str | None = None
    source_url: str | None = None
    image_url: str | None = None


class BomProviderPendingChoice(BaseModel):
    entry_id: UUID
    mpn: str
    candidates: list[BomProviderCandidate]


class BomProviderFailure(BaseModel):
    entry_id: UUID
    mpn: str
    reason: str


class BomProviderImportOut(BaseModel):
    created: int
    linked_existing: int = 0
    pending_choices: list[BomProviderPendingChoice] = []
    failures: list[BomProviderFailure] = []
    provider: str
    truncated: bool = False


# BOM presets (#252 — lifted from app/api/routes/bom_presets.py)

class PresetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    config: dict


class PresetPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    config: dict | None = None


# Project entry match (#252 — lifted from app/api/routes/projects.py)

class MatchEntryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_id: UUID
