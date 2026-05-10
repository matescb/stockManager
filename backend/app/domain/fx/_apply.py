"""Apply display FX conversion to sourcing offers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Literal, TypeVar

from app.core.time import utcnow
from app.domain.fx import rates as fx_rates
from app.domain.sourcing.schemas import (
    SourcingBomOfferOut,
    SourcingBomPriceBreakOut,
    SourcingConvertedPriceBreak,
    SourcingDistributor,
)

FxStatus = Literal["ok", "unavailable"]
FxOffer = TypeVar("FxOffer", SourcingDistributor, SourcingBomOfferOut)


def apply_fx_to_offer(
    offer: FxOffer,
    *,
    requested_currency: str | None,
    fetch_today_rates: Callable[[], dict[str, Decimal]] | None = None,
) -> tuple[FxOffer, FxStatus]:
    """Return an offer annotated with display-currency conversion fields."""
    requested = _clean_currency(requested_currency)
    native = _clean_currency(offer.currency)
    offer.currency_displayed = native
    if requested is None or native is None:
        return offer, "ok"
    if native == requested:
        offer.currency_displayed = requested
        return offer, "ok"
    if offer.unit_price is None:
        return offer, "ok"
    if fetch_today_rates is None:
        offer.fx_converted = None
        return offer, "unavailable"

    rate_date = utcnow().date()
    try:
        rates = fetch_today_rates()
    except fx_rates.FxRateError:
        offer.fx_converted = None
        return offer, "unavailable"

    converted = fx_rates.convert(
        _as_decimal(offer.unit_price),
        from_currency=native,
        to_currency=requested,
        rates=rates,
        on_date=rate_date,
    )
    if converted is None:
        offer.fx_converted = None
        return offer, "unavailable"

    converted_breaks = _converted_price_breaks(
        offer,
        from_currency=native,
        to_currency=requested,
        rates=rates,
        rate_date=rate_date,
    )
    if converted_breaks is None:
        offer.fx_converted = None
        return offer, "unavailable"

    offer.unit_price_converted = converted
    offer.currency_displayed = requested
    offer.fx_converted = True
    offer.fx_rate_date = rate_date
    offer.price_breaks_converted = converted_breaks
    return offer, "ok"


def _converted_price_breaks(
    offer: SourcingDistributor | SourcingBomOfferOut,
    *,
    from_currency: str,
    to_currency: str,
    rates: dict[str, Decimal],
    rate_date: date,
) -> list[SourcingConvertedPriceBreak] | list[SourcingBomPriceBreakOut] | None:
    converted_breaks: list[SourcingConvertedPriceBreak] | list[SourcingBomPriceBreakOut]
    converted_breaks = []
    for price_break in offer.price_breaks:
        converted_break = fx_rates.convert(
            _as_decimal(price_break.unit_price),
            from_currency=from_currency,
            to_currency=to_currency,
            rates=rates,
            on_date=rate_date,
        )
        if converted_break is None:
            return None
        if isinstance(offer, SourcingBomOfferOut):
            converted_breaks.append(
                SourcingBomPriceBreakOut(
                    quantity=price_break.quantity,
                    unit_price=converted_break,
                )
            )
        else:
            converted_breaks.append(
                SourcingConvertedPriceBreak(
                    quantity=price_break.quantity,
                    unit_price=converted_break,
                )
            )
    return converted_breaks


def _clean_currency(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().upper()
    return cleaned or None


def _as_decimal(value: Decimal | float | int | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
