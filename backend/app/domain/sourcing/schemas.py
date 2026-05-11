"""Pydantic DTOs for TrustedParts sourcing."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, get_args
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationInfo,
    field_validator,
)

MAX_DISTRIBUTORS = 25


class SourcingQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_token: str
    manufacturers: list[str] | None = None


class SourcingPriceBreak(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quantity: int
    unit_price: float
    formatted_amount: str | None = None
    text: str | None = None


class SourcingConvertedPriceBreak(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quantity: int
    unit_price: Decimal


class SourcingBomPriceBreakOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quantity: int
    unit_price: Decimal


class SourcingDistributor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    distributor_id: int | None = None
    name: str
    sku: str | None = None
    packaging: str | None = None
    moq: int | None = None
    lead_time_days: int | None = None
    stock: int | None = None
    unit_price: float | None = None
    currency: str | None = None
    unit_price_converted: Decimal | None = None
    currency_displayed: str | None = None
    fx_converted: bool | None = None
    fx_rate_date: date | None = None
    price_breaks: list[SourcingPriceBreak] = Field(default_factory=list)
    price_breaks_converted: list[SourcingConvertedPriceBreak] | None = None
    product_url: str | None = None
    rohs_compliance: list["SourcingRohsCompliance"] = Field(default_factory=list)
    availability_text: str | None = None
    quantity_multiple: int | None = None


class SourcingSpecification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    value: str


class SourcingRohsCompliance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: str
    is_compliant: bool
    description: str | None = None


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
    lifecycle_risk: str | None = None
    supply_chain_risk: str | None = None
    is_affected_by_tariff: bool | None = None
    manufacturer_id: int | None = None
    specifications: list[SourcingSpecification] = Field(default_factory=list)


class SourcingSearchRaw(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offers: list[SourcingOffer]
    request_id: str | None = None
    tp_current_date: datetime | None = None
    tp_response_time: str | None = None


class SourcingSearchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mpns: list[str] = Field(min_length=1, max_length=50)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    in_stock_only: bool = False
    distributors: list[str] | None = Field(default=None, max_length=MAX_DISTRIBUTORS)
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
    tp_current_date: datetime | None = None
    tp_response_time: str | None = None
    fetched_at: datetime
    cache_hit: bool


class SourcingSearchOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[SourcingSearchResult]
    request_id: str | None = None
    tp_current_date: datetime | None = None
    tp_response_time: str | None = None
    powered_by: Literal["TrustedParts"] = "TrustedParts"
    fetched_at: datetime
    cache_hit: bool
    links: SourcingAttributionLinks


class SourcingBomIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    build_quantity: int = Field(ge=1)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    distributors: list[str] | None = Field(default=None, max_length=MAX_DISTRIBUTORS)
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


SourcingAlertType = Literal[
    "stock_below",
    "stock_above",
    "back_in_stock",
    "out_of_authorized_stock",
    "price_changed",
    "bom_buyable",
    "lifecycle_risk_changed",
    "supply_chain_risk_changed",
    "tariff_status_changed",
]
SOURCING_ALERT_TYPE_VALUES = frozenset(get_args(SourcingAlertType))


class StockBelowThreshold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_type: Literal["stock_below"] = Field(default="stock_below", exclude=True)
    qty: int = Field(ge=0)


class StockAboveThreshold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_type: Literal["stock_above"] = Field(default="stock_above", exclude=True)
    qty: int = Field(ge=0)


class BackInStockThreshold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_type: Literal["back_in_stock"] = Field(default="back_in_stock", exclude=True)


class OutOfAuthorizedStockThreshold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_type: Literal["out_of_authorized_stock"] = Field(
        default="out_of_authorized_stock",
        exclude=True,
    )


class PriceChangedThreshold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_type: Literal["price_changed"] = Field(default="price_changed", exclude=True)
    delta_pct: Decimal = Field(gt=0, le=100)


class BomBuyableThreshold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_type: Literal["bom_buyable"] = Field(default="bom_buyable", exclude=True)
    build_quantity: int = Field(ge=1)


class StringChangedThreshold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    must_contain: str | None = None
    case_sensitive: bool = False


class TariffStatusChangedThreshold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_type: Literal["tariff_status_changed"] = Field(
        default="tariff_status_changed",
        exclude=True,
    )


class LifecycleRiskChangedThreshold(StringChangedThreshold):
    alert_type: Literal["lifecycle_risk_changed"] = Field(
        default="lifecycle_risk_changed",
        exclude=True,
    )


class SupplyChainRiskChangedThreshold(StringChangedThreshold):
    alert_type: Literal["supply_chain_risk_changed"] = Field(
        default="supply_chain_risk_changed",
        exclude=True,
    )


SourcingAlertThreshold = Annotated[
    StockBelowThreshold
    | StockAboveThreshold
    | BackInStockThreshold
    | OutOfAuthorizedStockThreshold
    | PriceChangedThreshold
    | BomBuyableThreshold
    | LifecycleRiskChangedThreshold
    | SupplyChainRiskChangedThreshold
    | TariffStatusChangedThreshold,
    Field(discriminator="alert_type"),
]


def _threshold_with_parent_alert_type(value: Any, info: ValidationInfo) -> Any:
    if isinstance(value, dict) and "alert_type" not in value and isinstance(info.data, dict):
        alert_type = info.data.get("alert_type")
        if alert_type is not None:
            return {"alert_type": alert_type, **value}
    return value


SourcingAlertThresholdIn = Annotated[
    SourcingAlertThreshold,
    BeforeValidator(_threshold_with_parent_alert_type),
]

_THRESHOLD_ADAPTER = TypeAdapter(SourcingAlertThreshold)


def validate_alert_threshold(
    alert_type: SourcingAlertType | str,
    threshold: SourcingAlertThreshold | dict[str, Any],
) -> SourcingAlertThreshold:
    if isinstance(threshold, BaseModel) and getattr(threshold, "alert_type", None) == alert_type:
        return threshold
    if isinstance(threshold, BaseModel):
        threshold = threshold.model_dump(mode="python")
    return _THRESHOLD_ADAPTER.validate_python({"alert_type": alert_type, **threshold})


def dump_alert_threshold(
    alert_type: SourcingAlertType | str,
    threshold: SourcingAlertThreshold | dict[str, Any],
) -> dict[str, Any]:
    return validate_alert_threshold(alert_type, threshold).model_dump(mode="json")


class SourcingAlertIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_type: SourcingAlertType
    part_id: UUID | None = None
    project_id: UUID | None = None
    threshold: SourcingAlertThresholdIn
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    currency_code: str | None = Field(default=None, min_length=3, max_length=3)
    distributor_filter: list[str] | None = Field(default=None, max_length=MAX_DISTRIBUTORS)
    notify_user_ids: list[UUID] | None = None
    cooldown_seconds: int = Field(default=86400, ge=60)
    enabled: bool = True

    @field_validator("country_code", "currency_code")
    @classmethod
    def _uppercase_codes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().upper()

    @field_validator("distributor_filter")
    @classmethod
    def _strip_distributor_filter(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        stripped = [item.strip() for item in value if item.strip()]
        return stripped or None


class SourcingAlertPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_type: SourcingAlertType | None = None
    part_id: UUID | None = None
    project_id: UUID | None = None
    threshold: dict[str, Any] | None = None
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    currency_code: str | None = Field(default=None, min_length=3, max_length=3)
    distributor_filter: list[str] | None = Field(default=None, max_length=MAX_DISTRIBUTORS)
    notify_user_ids: list[UUID] | None = None
    cooldown_seconds: int | None = Field(default=None, ge=60)
    enabled: bool | None = None

    @field_validator("country_code", "currency_code")
    @classmethod
    def _uppercase_codes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().upper()

    @field_validator("distributor_filter")
    @classmethod
    def _strip_distributor_filter(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        stripped = [item.strip() for item in value if item.strip()]
        return stripped or None


class SourcingAlertOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    workspace_id: UUID
    alert_type: SourcingAlertType
    part_id: UUID | None = None
    project_id: UUID | None = None
    threshold: dict[str, Any]
    country_code: str | None = None
    currency_code: str | None = None
    distributor_filter: list[str] | None = None
    notify_user_ids: list[UUID] | None = None
    cooldown_seconds: int
    enabled: bool
    last_checked_at: datetime | None = None
    last_notified_at: datetime | None = None
    archived_at: datetime | None = None
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class SourcingAlertListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SourcingAlertOut]
    total: int
    limit: int
    offset: int


class SourcingBomOfferOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mpn: str
    distributor: str
    sku: str | None = None
    stock: int
    unit_price: Decimal | None = None
    currency: str | None = None
    unit_price_converted: Decimal | None = None
    currency_displayed: str | None = None
    fx_converted: bool | None = None
    fx_rate_date: date | None = None
    packaging: str | None = None
    moq: int | None = None
    lead_time_days: int | None = None
    price_breaks: list[SourcingBomPriceBreakOut] = Field(default_factory=list)
    price_breaks_converted: list[SourcingBomPriceBreakOut] | None = None
    url: str | None = None
    availability_text: str | None = None
    quantity_multiple: int | None = None
    lifecycle_risk: str | None = None
    supply_chain_risk: str | None = None
    is_affected_by_tariff: bool | None = None
    rohs_compliance: list[SourcingRohsCompliance] = Field(default_factory=list)
    manufacturer_id: int | None = None
    specifications: list[SourcingSpecification] = Field(default_factory=list)


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
    lowest_total_price_combo: list[str]
    lowest_total_price_total: Decimal | None
    fewest_distributors_combo: list[str]
    fewest_distributors_total: Decimal | None
    target_coverage_pct: float


class BuildCapacityOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    can_build_now: int
    can_build_after_purchase: int
    total_bom_cost: Decimal | None = Field(
        default=None,
        description=(
            "Sum of required quantity times best-offer unit price across priced "
            "BOM lines, independent of on-hand stock."
        ),
    )
    cost_per_single_bom: Decimal | None = Field(
        default=None,
        description=(
            "Cost of building one complete BOM unit, derived from total_bom_cost "
            "divided by the requested build quantity."
        ),
    )
    purchase_to_pay_cost: Decimal | None = Field(
        default=None,
        description=(
            "Sum of short quantity times best-offer unit price across priced "
            "lines that are not blocking after authorized supply."
        ),
    )
    est_purchase_cost: Decimal | None = Field(
        default=None,
        deprecated=True,
        description="Deprecated alias for purchase_to_pay_cost; remove after SX-8.",
    )
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


class PurchasePlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    build_quantity: int = Field(ge=1)
    strategy: Literal[
        "lowest_total_price",
        "fewest_distributors",
        "fastest_availability",
        "preferred_first",
    ] = "preferred_first"
    country: str | None = Field(default=None, min_length=2, max_length=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    distributors: list[str] | None = Field(default=None, max_length=MAX_DISTRIBUTORS)
    max_distributors: int | None = Field(default=None, ge=1)
    moq_overbuy_cap: int | None = Field(default=None, ge=1)
    price_tolerance_pct: Decimal = Field(default=Decimal("5"), ge=0)

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


class PurchasePlanOrderOverrideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_distributor: str = Field(min_length=1, max_length=120)
    selected_qty: int = Field(ge=1)
    selected_unit_price: Decimal
    selected_currency: str = Field(min_length=3, max_length=3)

    @field_validator("selected_distributor")
    @classmethod
    def _strip_distributor(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("selected_distributor must not be empty")
        return stripped

    @field_validator("selected_currency")
    @classmethod
    def _uppercase_currency(cls, value: str) -> str:
        stripped = value.strip().upper()
        if len(stripped) != 3:
            raise ValueError("selected_currency must be a 3-letter code")
        return stripped


class PurchasePlanOrdersIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overrides: dict[UUID, PurchasePlanOrderOverrideIn] = Field(default_factory=dict)


class PurchasePlanLineOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    project_entry_id: UUID | None
    part_id: UUID
    mpn_searched: str
    required_qty: int
    internal_available_qty: int
    shortage_qty: int
    selected_distributor: str | None = None
    selected_qty: int | None = None
    selected_unit_price: Decimal | None = None
    selected_currency: str | None = None
    selected_packaging: str | None = None
    selected_moq: int | None = None
    selected_lead_time_days: int | None = None
    selected_url: str | None = None
    available_offers: list[SourcingBomOfferOut] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)


class PurchasePlanOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    project_id: UUID
    build_quantity: int
    strategy: str
    country_code: str | None = None
    currency_code: str | None = None
    preferred_distributors: list[str] | None = None
    max_distributors: int | None = None
    moq_overbuy_cap: int | None = None
    price_tolerance_pct: Decimal | None = None
    status: str
    created_at: datetime
    expires_at: datetime
    last_refreshed_at: datetime | None = None
    created_by: UUID | None = None
    lines: list[PurchasePlanLineOut]
    distributors_used: list[str]
    est_total_cost: Decimal | None = None
    worst_lead_time_days: int | None = None
    unfilled_count: int


class PurchasePlanCreatedOrderEntryOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    part_id: UUID | None = None
    quantity_ordered: int
    unit_price: Decimal | None = None
    currency: str | None = None
    comments: str | None = None


class PurchasePlanCreatedOrderOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    name: str
    supplier: str | None = None
    status: str
    currency: str | None = None
    comments: str | None = None
    entries: list[PurchasePlanCreatedOrderEntryOut]


class PurchasePlanOrdersOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    orders: list[PurchasePlanCreatedOrderOut]


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
    cache_hit: bool | None = None
    reason: Literal["ok", "no_mpn", "no_offers"] = "ok"
    fx_status: Literal["unavailable"] | None = None
    risk_flags: list[
        Literal[
            "single_source",
            "no_authorized_stock",
            "moq_overbuy",
            "lead_time_long",
            "preferred_distributor_unmet",
            "lifecycle_risk_present",
            "supply_chain_risk_present",
            "tariff_affected",
            "rohs_non_compliant",
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
    build_quantity: int
    powered_by: Literal["TrustedParts"] = "TrustedParts"
    fetched_at: datetime
    partial: bool
    links: SourcingAttributionLinks
    fx_status: Literal["ok", "partial", "unavailable"] | None = None
