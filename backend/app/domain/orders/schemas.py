from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class OrderEntryIn(BaseModel):
    part_id: UUID | None = None
    name: str | None = None
    quantity_ordered: int = Field(ge=0)
    unit_price: Decimal | None = None
    currency: str | None = None
    comments: str | None = None


class OrderEntryPatch(BaseModel):
    part_id: UUID | None = None
    name: str | None = None
    quantity_ordered: int | None = None
    unit_price: Decimal | None = None
    currency: str | None = None
    comments: str | None = None


class OrderCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    order_type: Literal["purchase", "sales"] = "purchase"
    supplier: str | None = None
    ordered_on: date | None = None
    expected_on: date | None = None
    currency: str | None = None
    comments: str | None = None
    entries: list[OrderEntryIn] = []


class OrderPatchIn(BaseModel):
    name: str | None = None
    supplier: str | None = None
    status: Literal["draft", "open", "partial", "received", "cancelled"] | None = None
    ordered_on: date | None = None
    expected_on: date | None = None
    received_on: date | None = None
    currency: str | None = None
    comments: str | None = None


class ReceiveLineIn(BaseModel):
    order_entry_id: UUID
    quantity: int = Field(gt=0)
    storage_location_id: UUID | None = None
    lot_name: str | None = None
    serial_number: str | None = None


class ReceiveIn(BaseModel):
    received_on: date | None = None
    lines: list[ReceiveLineIn] = Field(min_length=1)
