from __future__ import annotations

from decimal import Decimal

from app.domain.fx._apply import apply_fx_to_offer
from app.domain.sourcing.schemas import SourcingDistributor, SourcingPriceBreak


def _rates() -> dict[str, Decimal]:
    return {
        "EUR": Decimal("1"),
        "USD": Decimal("2"),
        "GBP": Decimal("0.5"),
    }


def test_apply_fx_converts_unit_price():
    offer = SourcingDistributor(name="DigiKey", unit_price=2.0, currency="USD")

    converted, status = apply_fx_to_offer(
        offer,
        requested_currency="EUR",
        fetch_today_rates=_rates,
    )

    assert status == "ok"
    assert converted.unit_price == 2.0
    assert converted.unit_price_converted == Decimal("1.0000")
    assert converted.currency == "USD"
    assert converted.currency_displayed == "EUR"
    assert converted.fx_converted is True
    assert converted.fx_rate_date is not None


def test_apply_fx_converts_each_price_break():
    offer = SourcingDistributor(
        name="DigiKey",
        unit_price=2.0,
        currency="USD",
        price_breaks=[
            SourcingPriceBreak(quantity=1, unit_price=2.0),
            SourcingPriceBreak(quantity=10, unit_price=1.5),
        ],
    )

    converted, status = apply_fx_to_offer(
        offer,
        requested_currency="EUR",
        fetch_today_rates=_rates,
    )

    assert status == "ok"
    assert converted.price_breaks_converted is not None
    assert [item.quantity for item in converted.price_breaks_converted] == [1, 10]
    assert [item.unit_price for item in converted.price_breaks_converted] == [
        Decimal("1.0000"),
        Decimal("0.7500"),
    ]


def test_apply_fx_preserves_native_when_rate_unavailable():
    offer = SourcingDistributor(
        name="DigiKey",
        unit_price=2.0,
        currency="AUD",
    )

    converted, status = apply_fx_to_offer(
        offer,
        requested_currency="EUR",
        fetch_today_rates=_rates,
    )

    assert status == "unavailable"
    assert converted.unit_price == 2.0
    assert converted.unit_price_converted is None
    assert converted.currency_displayed == "AUD"
    assert converted.fx_converted is None


def test_apply_fx_no_op_when_currencies_already_match():
    offer = SourcingDistributor(
        name="DigiKey",
        unit_price=2.0,
        currency="EUR",
    )

    converted, status = apply_fx_to_offer(
        offer,
        requested_currency="EUR",
        fetch_today_rates=_rates,
    )

    assert status == "ok"
    assert converted.unit_price == 2.0
    assert converted.unit_price_converted is None
    assert converted.currency_displayed == "EUR"
    assert converted.fx_converted is None
