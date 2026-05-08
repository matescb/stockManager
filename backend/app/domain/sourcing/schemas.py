"""Pydantic DTOs for TrustedParts sourcing."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SourcingQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_token: str
    manufacturers: list[str] | None = None


class SourcingPriceBreak(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quantity: int
    unit_price: float


class SourcingBomPriceBreakOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quantity: int
    unit_price: Decimal


class SourcingDistributor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    sku: str | None = None
    packaging: str | None = None
    moq: int | None = None
    lead_time_days: int | None = None
    stock: int | None = None
    unit_price: float | None = None
    currency: str | None = None
    price_breaks: list[SourcingPriceBreak] = Field(default_factory=list)
    product_url: str | None = None


class SourcingLinks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: str | None = None
    manufacturer: str | None = None
    datasheet: str | None = None


class SourcingOffer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mpn: str
    manufacturer: str | None = None
    description: str | None = None
    distributors: list[SourcingDistributor] = Field(default_factory=list)
    links: SourcingLinks = Field(default_factory=SourcingLinks)


class SourcingSearchRaw(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offers: list[SourcingOffer]
    request_id: str | None = None


class SourcingSearchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mpns: list[str] = Field(min_length=1, max_length=50)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    in_stock_only: bool = False
    distributors: list[str] | None = None
    use_cached_data: bool | None = None

    @field_validator("mpns")
    @classmethod
    def _strip_mpns(cls, value: list[str]) -> list[str]:
        stripped = [item.strip() for item in value]
        if any(not item for item in stripped):
            raise ValueError("mpns must not contain empty values")
        return stripped

    @field_validator("country", "currency")
    @classmethod
    def _uppercase_codes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().upper()

    @field_validator("distributors")
    @classmethod
    def _strip_distributors(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        stripped = [item.strip() for item in value if item.strip()]
        return stripped or None


class SourcingAttributionLinks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: str
    attribution: str


class SourcingSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mpn: str
    offers: list[SourcingOffer] = Field(default_factory=list)
    request_id: str | None = None
    fetched_at: datetime
    cache_hit: bool


class SourcingSearchOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[SourcingSearchResult]
    request_id: str | None = None
    powered_by: Literal["TrustedParts"] = "TrustedParts"
    fetched_at: datetime
    cache_hit: bool
    links: SourcingAttributionLinks


class SourcingBomIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    build_quantity: int = Field(ge=1)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    distributors: list[str] | None = None
    in_stock_only: bool = False
    use_cached_data: bool | None = None

    @field_validator("country", "currency")
    @classmethod
    def _uppercase_codes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().upper()

    @field_validator("distributors")
    @classmethod
    def _strip_distributors(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        stripped = [item.strip() for item in value if item.strip()]
        return stripped or None


class SourcingBomOfferOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mpn: str
    distributor: str
    sku: str | None = None
    stock: int
    unit_price: Decimal | None = None
    currency: str | None = None
    packaging: str | None = None
    moq: int | None = None
    lead_time_days: int | None = None
    price_breaks: list[SourcingBomPriceBreakOut] = Field(default_factory=list)
    url: str | None = None


class DistributorCoverageRowOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    distributor: str
    lines_covered: int
    lines_uncovered: list[UUID]
    coverage_pct: float
    est_total_cost: Decimal | None
    worst_lead_time_days: int | None


class DistributorCoverageMatrixOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    rows: list[DistributorCoverageRowOut]
    total_lines: int
    best_single_distributor: str | None
    best_two_distributor_combo: tuple[str, str] | None


class BuildCapacityOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    can_build_now: int
    can_build_after_purchase: int
    est_purchase_cost: Decimal | None
    blocking_lines_now: list[UUID]
    blocking_lines_after_purchase: list[UUID]


class OptimizerSelectionOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    project_entry_id: UUID
    part_id: UUID
    mpn_searched: str
    required_qty: int
    internal_available_qty: int
    shortage_qty: int
    selected_distributor: str | None = None
    selected_qty: int
    selected_unit_price: Decimal | None = None
    selected_currency: str | None = None
    selected_packaging: str | None = None
    selected_moq: int | None = None
    selected_lead_time_days: int | None = None
    selected_url: str | None = None
    risk_flags: tuple[str, ...] = ()


class OptimizerOutcomeOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    selections: list[OptimizerSelectionOut]
    unfilled_lines: list[UUID]
    distributors_used: list[str]
    est_total_cost: Decimal | None = None
    worst_lead_time_days: int | None = None


class SourcingBomLineOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_entry_id: UUID
    part_id: UUID
    part_name: str
    mpn: str | None = None
    required: int
    available: int
    substitute_ids: list[UUID] = Field(default_factory=list)
    substitute_available: int
    short_by: int
    authorized_stock: int
    offers: list[SourcingBomOfferOut] = Field(default_factory=list)
    best_offer: SourcingBomOfferOut | None = None
    est_extended_cost: Decimal | None = None
    lead_time_days: int | None = None
    risk_flags: list[
        Literal[
            "single_source",
            "no_authorized_stock",
            "moq_overbuy",
            "lead_time_long",
            "preferred_distributor_unmet",
        ]
    ] = Field(default_factory=list)


class SourcingReportData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorized_stock: int
    offers: list[SourcingBomOfferOut] = Field(default_factory=list)
    best_offer: SourcingBomOfferOut | None = None
    est_replenishment_cost: Decimal | None = None
    lead_time_days: int | None = None
    preferred_distributor_available: bool
    cache_hit: bool
    fetched_at: datetime


class SourcingBomOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[SourcingBomLineOut]
    coverage: DistributorCoverageMatrixOut
    capacity: BuildCapacityOut
    powered_by: Literal["TrustedParts"] = "TrustedParts"
    fetched_at: datetime
    partial: bool
    links: SourcingAttributionLinks
