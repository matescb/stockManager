"""Pydantic DTOs for TrustedParts sourcing."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


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
