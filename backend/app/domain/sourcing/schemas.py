"""Pydantic DTOs for TrustedParts sourcing."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SourcingQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_token: str
    manufacturers: list[str] | None = None


class SourcingPriceBreak(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quantity: int
    unit_price: float


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
