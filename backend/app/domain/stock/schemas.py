from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Constrained type for bag_signature: must be a 64-char lowercase hex string
# (SHA-256 digest), or None.  The max_length=64 check that existed before is
# replaced by this stricter pattern so an adversarial or buggy client cannot
# persist an arbitrary alphanumeric blob that would corrupt rescan recognition.
BagSignatureStr = Annotated[
    str,
    StringConstraints(pattern=r"^[a-f0-9]{64}$"),
]


class PriceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["none", "per_component", "entire_lot"] = "none"
    unit_price: Decimal | None = None
    total_price: Decimal | None = None
    currency: str | None = None


class LotInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    comments: str | None = None
    expiration_date: str | None = None  # ISO date
    serial_number: str | None = None


class AddStockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_id: UUID
    quantity: int = Field(gt=0)
    storage_location_id: UUID | None = None
    price: PriceInput | None = None
    lot: LotInput | None = None
    comments: str | None = None
    # Set by /parts/bulk-import-from-scan to record which physical bag
    # produced this entry (sha256 of the normalised raw bag code). Other
    # callers leave it None — the manual add-stock flow doesn't have a
    # bag identity.
    bag_signature: BagSignatureStr | None = None
    # Optional raw bag code — when supplied alongside bag_signature the
    # server recomputes the digest and rejects the request (422) if the
    # two disagree, protecting against a buggy or adversarial client.
    # When absent, bag_signature is accepted verbatim (back-compat).
    raw_bag_code: str | None = Field(default=None, max_length=4096)


class RemoveStockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_id: UUID
    quantity: int = Field(gt=0)
    storage_location_id: UUID | None = None
    lot_id: UUID | None = None
    comments: str | None = None


class MoveStockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_id: UUID
    source_storage_location_id: UUID | None = None
    source_lot_id: UUID | None = None
    destination_storage_location_id: UUID
    quantity: int = Field(gt=0)
    split_lot: bool = False
    comments: str | None = None


class AdjustStockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_id: UUID
    storage_location_id: UUID | None = None
    lot_id: UUID | None = None
    actual_quantity: int = Field(ge=0)
    comments: str | None = None


class StockEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    part_id: UUID | None
    lot_id: UUID | None
    storage_location_id: UUID | None
    quantity_delta: int
    status: str
    unit_price: Decimal | None
    currency: str | None
    operation_type: str
    comments: str | None
    occurred_at: datetime


class StockSummaryRow(BaseModel):
    part_id: UUID
    storage_location_id: UUID | None
    lot_id: UUID | None
    quantity: int
    unit_price: Decimal | None = None
    currency: str | None = None


# ---------------------------------------------------------------------------
# Box intake / box audit (phase 15). One scanned bag = one lot, so a box's
# contents are a list of (part, lot) buckets that can be checked bag by bag.
# ---------------------------------------------------------------------------


class BoxScanResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mpn: str | None = Field(default=None, max_length=200)
    bag_signature: BagSignatureStr | None = None
    raw_bag_code: str | None = Field(default=None, max_length=4096)
    # Off for the audit page's first pass: a bag it does not know is
    # resolved again with the provider only when the operator keeps it.
    provider_lookup: bool = True


class BoxIntakeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_id: UUID | None = None
    mpn: str | None = Field(default=None, max_length=200)
    quantity: int = Field(gt=0)
    bag_signature: BagSignatureStr | None = None
    raw_bag_code: str | None = Field(default=None, max_length=4096)
    lot_name: str | None = Field(default=None, max_length=200)
    lot_serial: str | None = Field(default=None, max_length=200)
    comments: str | None = Field(default=None, max_length=1000)
    # What to do when the scanned bag is already recorded in another box:
    # "move" relocates that lot here (the default — the bag was physically
    # moved), "add_new" records it as a fresh bag (it is a second bag
    # that happens to carry an identical label).
    on_existing: Literal["move", "add_new"] = "move"


class BoxAuditLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_id: UUID
    lot_id: UUID | None = None
    action: Literal["count", "write_off", "move"]
    actual_quantity: int | None = Field(default=None, ge=0)
    to_storage_location_id: UUID | None = None


class BoxAuditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[BoxAuditLine] = Field(min_length=1, max_length=1000)
    comments: str | None = Field(default=None, max_length=500)
