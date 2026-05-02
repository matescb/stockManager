"""Pydantic input schemas for the parts domain (CQ-006 / issue #122).

Lifted out of `app/api/routes/parts.py` so:
* every domain has one canonical `domain/<x>/schemas.py` (the rule
  established in `docs/ARCHITECTURE.md`),
* schemas can be re-used by other routers without an import cycle
  through `routes.parts`,
* issue #118's split of the 1245-line parts router has somewhere to
  land things.

Every input schema keeps `model_config = ConfigDict(extra="forbid")` —
`tests/test_extra_forbid.py` regression-tests this and a silent drop
would let unknown fields through.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.stock.schemas import BagSignatureStr

__all__ = [
    "PartIn",
    "PartPatch",
    "BulkDeleteIn",
    "SubstituteIn",
    "MetaMemberIn",
    "ScanImportRow",
    "ScanImportIn",
    "QuickRemoveBagIn",
    "LookupIn",
]


class PartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_type: Literal["linked", "local", "meta", "sub_assembly"] = "local"
    # Optional — defaults to mpn server-side when blank, so the operator can
    # paste an MPN and skip the name field. At least one of name/mpn must be
    # set; the create endpoint enforces that explicitly.
    name: str | None = Field(default=None, max_length=300)
    manufacturer: str | None = None
    mpn: str | None = None
    internal_part_number: str | None = None
    description: str | None = None
    notes_markdown: str | None = None
    footprint: str | None = None
    low_stock_report_quantity: int | None = None
    attrition_percentage: float = 0
    attrition_min_quantity: int = 0
    default_storage_location_id: UUID | None = None
    default_storage_mandatory: bool = False
    serialized: bool = False


class PartPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    manufacturer: str | None = None
    mpn: str | None = None
    internal_part_number: str | None = None
    description: str | None = None
    notes_markdown: str | None = None
    footprint: str | None = None
    low_stock_report_quantity: int | None = None
    attrition_percentage: float | None = None
    attrition_min_quantity: int | None = None
    default_storage_location_id: UUID | None = None
    default_storage_mandatory: bool | None = None
    serialized: bool | None = None
    published: bool | None = None
    # Command flag: when true, drops the provider link, clears
    # last_refresh_at, resets description_locally_edited, and converts
    # every {provider, override} custom_field row on this part to
    # `manual` (override rows lose their original_value).
    unlink_provider: bool | None = None


class BulkDeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 100 is plenty for a single-shot multi-select; if a user wants to wipe
    # more they can run it twice. Keeps the response payload bounded.
    part_ids: list[UUID] = Field(min_length=1, max_length=100)


class SubstituteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    substitute_part_id: UUID
    direction: Literal["one_way", "bidirectional"] = "bidirectional"


class MetaMemberIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_part_id: UUID


class ScanImportRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mpn: str = Field(min_length=1, max_length=200)
    quantity: int | None = Field(default=None, ge=0)
    storage_location_id: UUID | None = None
    # Traceability fields lifted from the bag's 2D code. The frontend
    # synthesises these strings from the parsed DIs (10D/1T → lot_name,
    # K/1K/14K/11K → comments). All optional — the import works without
    # them, you just lose the audit trail.
    lot_name: str | None = Field(default=None, max_length=200)
    lot_serial: str | None = Field(default=None, max_length=200)
    comments: str | None = Field(default=None, max_length=1000)
    # sha256 hex of the normalised raw bag code. When the workspace has
    # already imported a bag with this signature, the row resolves with
    # status='bag_rescan' carrying the prior import's part/lot/location/qty
    # so the frontend can offer an inline "remove qty from this bag"
    # affordance instead of double-importing.
    bag_signature: BagSignatureStr | None = None
    # Optional raw bag code for server-side signature verification.  When
    # present the server recomputes the digest and rejects the row with
    # status='bag_signature_mismatch' if it disagrees with bag_signature.
    # When absent, bag_signature is accepted verbatim (back-compat).
    raw_bag_code: str | None = Field(default=None, max_length=4096)


class ScanImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Row cap at 50: bounds worst-case wall-clock latency at ~300 ms/lookup
    # × 50 = 25 s peak, well inside a 60 s deadline budget. Typical bag
    # deliveries are under 50 unique MPNs; operators wanting larger imports
    # can split across multiple calls (BE2-003).
    rows: list[ScanImportRow] = Field(min_length=1, max_length=50)
    # Optional FE-supplied idempotency key (UUID4 generated once per submit
    # attempt, re-sent unchanged on retry). When absent the server derives
    # a content-hash from the row contents. A retry with the same key
    # returns the cached envelope without creating new Parts (BE2-003).
    idempotency_key: str | None = Field(default=None, max_length=64)


class QuickRemoveBagIn(BaseModel):
    """Inline-consume from a recognised re-scanned bag. The frontend
    sends back the lot/location/qty hint it got from the bag_rescan
    row in /bulk-import-from-scan; we run remove_stock with those
    coordinates targeting the named lot+location."""

    model_config = ConfigDict(extra="forbid")

    quantity: int = Field(gt=0)
    lot_id: UUID | None = None
    storage_location_id: UUID | None = None
    comments: str | None = Field(default=None, max_length=1000)


# Provider lookup (#252 — lifted from app/api/routes/parts_provider.py)

class LookupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mpn: str = Field(min_length=1, max_length=200)
