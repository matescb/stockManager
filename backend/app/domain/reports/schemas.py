"""Pydantic DTOs for reports."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.sourcing.schemas import SourcingAttributionLinks, SourcingBomOfferOut

SourcingReportStatus = Literal["ok", "not_configured", "partial", "budget_blocked"]
ReplenishmentCostSort = Literal["delta_pct", "delta_abs", "name"]
ReplenishmentCostReason = Literal[
    "no_offer",
    "currency_mismatch",
    "sourcing_not_configured",
    "sourcing_unavailable",
]

SourcingRiskFlag = Literal[
    "single_source",
    "no_authorized_stock",
    "moq_overbuy",
    "lead_time_long",
    "preferred_distributor_unmet",
    "price_delta",
]


class ProjectBuyabilityRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    project_name: str
    build_quantity: int
    can_build_now: int
    can_build_after_purchase: int
    blocking_lines_count: int
    est_purchase_cost: Decimal | None = None
    partial: bool = False


class BomBuyabilityReportOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    build_quantity: int = Field(ge=1)
    rows: list[ProjectBuyabilityRow]
    sourcing_status: SourcingReportStatus
    truncated: bool
    project_cap: int
    powered_by: Literal["TrustedParts"] = "TrustedParts"
    links: SourcingAttributionLinks


class CurrencyAmountOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str | None
    value: Decimal


class ReplenishmentCostStatusOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["ok", "not_configured", "degraded", "error"]
    message: str | None = None
    fetched_at: datetime | None = None
    cache_hit: bool | None = None
    partial: bool = False
    powered_by: Literal["TrustedParts"] = "TrustedParts"
    links: SourcingAttributionLinks | None = None


class ReplenishmentCostRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_id: UUID
    name: str
    manufacturer: str | None = None
    mpn: str
    on_hand: int = Field(ge=1)
    currency: str | None = None
    historical_costs: list[CurrencyAmountOut]
    historical_cost: Decimal | None = None
    replacement_unit_price: Decimal | None = None
    replacement_cost: Decimal | None = None
    replacement_currency: str | None = None
    delta_abs: Decimal | None = None
    delta_pct: Decimal | None = None
    reason: ReplenishmentCostReason | None = None
    source: Literal["trustedparts"] | None = None


class ReplenishmentCostTotalOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str | None
    historical_cost: Decimal
    replacement_cost: Decimal
    delta_abs: Decimal | None


class ReplenishmentCostReportOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[ReplenishmentCostRow]
    totals: list[ReplenishmentCostTotalOut]
    sourcing_status: ReplenishmentCostStatusOut


class SourcingRiskStatusOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["ok", "not_configured", "budget_blocked", "upstream_error"]
    message: str


class SourcingRiskRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_id: UUID
    name: str
    manufacturer: str | None = None
    mpn: str
    on_hand: int
    distributors_with_stock: list[str] = Field(default_factory=list)
    authorized_stock: int
    best_offer: SourcingBomOfferOut | None = None
    lead_time_days: int | None = None
    typical_reorder_quantity: int
    historical_unit_cost: Decimal | None = None
    historical_currency: str | None = None
    price_delta_pct: Decimal | None = None
    risk_flags: list[SourcingRiskFlag] = Field(default_factory=list)


class SourcingRiskReportOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[SourcingRiskRow]
    sourcing_status: SourcingRiskStatusOut
    powered_by: Literal["TrustedParts"] = "TrustedParts"
    fetched_at: datetime
    partial: bool
    cache_hit: bool | None = None
    links: SourcingAttributionLinks
