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
    part_id: UUID
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
