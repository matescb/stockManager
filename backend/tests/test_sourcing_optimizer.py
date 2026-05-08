from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from app.domain.sourcing.optimizer import optimize
from app.domain.sourcing.schemas import (
    OptimizerOutcomeOut,
    SourcingBomLineOut,
    SourcingBomOfferOut,
    SourcingBomPriceBreakOut,
)


def _uuid(index: int) -> UUID:
    return UUID(f"00000000-0000-0000-0000-{index:012d}")


def _offer(
    distributor: str,
    *,
    mpn: str = "MPN",
    stock: int = 100,
    unit_price: str = "1.00",
    currency: str = "EUR",
    packaging: str | None = "cut-tape",
    moq: int | None = 1,
    lead_time_days: int | None = 3,
    price_breaks: list[tuple[int, str]] | None = None,
    url: str | None = None,
) -> SourcingBomOfferOut:
    return SourcingBomOfferOut(
        mpn=mpn,
        distributor=distributor,
        stock=stock,
        unit_price=Decimal(unit_price),
        currency=currency,
        packaging=packaging,
        moq=moq,
        lead_time_days=lead_time_days,
        price_breaks=[
            SourcingBomPriceBreakOut(quantity=quantity, unit_price=Decimal(price))
            for quantity, price in (price_breaks or [(1, unit_price)])
        ],
        url=url or f"https://trustedparts.test/{mpn}/{distributor}",
    )


def _line(
    index: int,
    *,
    short_by: int = 5,
    available: int = 0,
    substitute_available: int = 0,
    offers: list[SourcingBomOfferOut] | None = None,
    risk_flags: list[str] | None = None,
) -> SourcingBomLineOut:
    effective_offers = offers or []
    return SourcingBomLineOut(
        project_entry_id=_uuid(index),
        part_id=_uuid(index + 1000),
        part_name=f"Line {index}",
        mpn=f"MPN-{index}",
        required=short_by + available,
        available=available,
        substitute_available=substitute_available,
        short_by=short_by,
        authorized_stock=sum(offer.stock for offer in effective_offers),
        offers=effective_offers,
        risk_flags=risk_flags or [],
    )


def test_lowest_total_price_picks_cheapest_per_line():
    outcome = optimize(
        [
            _line(
                1,
                short_by=10,
                offers=[
                    _offer("DigiKey", unit_price="1.50"),
                    _offer("Mouser", unit_price="1.00"),
                ],
            )
        ],
        strategy="lowest_total_price",
    )

    assert outcome.selections[0].selected_distributor == "Mouser"
    assert outcome.selections[0].selected_qty == 10
    assert outcome.est_total_cost == Decimal("10.00")


def test_lowest_total_price_breaks_ties_by_alphabetical_distributor():
    outcome = optimize(
        [
            _line(
                1,
                offers=[
                    _offer("Zed", unit_price="1.00"),
                    _offer("Alpha", unit_price="1.00"),
                ],
            )
        ],
        strategy="lowest_total_price",
    )

    assert outcome.selections[0].selected_distributor == "Alpha"


def test_fewest_distributors_greedy_covers_most_first():
    outcome = optimize(
        [
            _line(1, offers=[_offer("D1"), _offer("D2")]),
            _line(2, offers=[_offer("D1")]),
            _line(3, offers=[_offer("D3")]),
        ],
        strategy="fewest_distributors",
    )

    assert [selection.selected_distributor for selection in outcome.selections] == [
        "D1",
        "D1",
        "D3",
    ]
    assert outcome.distributors_used == ["D1", "D3"]


def test_fewest_distributors_respects_max_distributors_constraint():
    outcome = optimize(
        [
            _line(1, offers=[_offer("D1")]),
            _line(2, offers=[_offer("D2")]),
        ],
        strategy="fewest_distributors",
        max_distributors=1,
    )

    assert outcome.selections[0].selected_distributor == "D1"
    assert outcome.selections[1].selected_distributor is None
    assert outcome.unfilled_lines == [_uuid(2)]


def test_fastest_availability_minimizes_lead_time_then_price():
    outcome = optimize(
        [
            _line(
                1,
                offers=[
                    _offer("Slow", unit_price="0.50", lead_time_days=10),
                    _offer("FastB", unit_price="2.00", lead_time_days=2),
                    _offer("FastA", unit_price="1.50", lead_time_days=2),
                ],
            )
        ],
        strategy="fastest_availability",
    )

    assert outcome.selections[0].selected_distributor == "FastA"
    assert outcome.worst_lead_time_days == 2


def test_preferred_first_respects_5pct_tolerance():
    outcome = optimize(
        [
            _line(
                1,
                offers=[
                    _offer("Cheap", unit_price="1.00"),
                    _offer("Preferred", unit_price="1.04"),
                ],
            )
        ],
        strategy="preferred_first",
        preferred_distributors=["Preferred"],
    )

    assert outcome.selections[0].selected_distributor == "Preferred"


def test_preferred_first_falls_through_to_cheapest_when_outside_tolerance():
    outcome = optimize(
        [
            _line(
                1,
                offers=[
                    _offer("Cheap", unit_price="1.00"),
                    _offer("Preferred", unit_price="1.06"),
                ],
            )
        ],
        strategy="preferred_first",
        preferred_distributors=["Preferred"],
    )

    assert outcome.selections[0].selected_distributor == "Cheap"


def test_moq_rounding_up():
    outcome = optimize(
        [
            _line(
                1,
                short_by=7,
                offers=[_offer("DigiKey", unit_price="2.00", moq=25, stock=50)],
            )
        ],
        strategy="lowest_total_price",
    )

    selection = outcome.selections[0]
    assert selection.selected_qty == 25
    assert selection.selected_moq == 25
    assert outcome.est_total_cost == Decimal("50.00")


def test_moq_overbuy_cap_marks_line_unfilled():
    outcome = optimize(
        [
            _line(
                1,
                short_by=7,
                offers=[_offer("DigiKey", unit_price="2.00", moq=25, stock=50)],
            )
        ],
        strategy="lowest_total_price",
        moq_overbuy_cap=3,
    )

    assert outcome.selections[0].selected_distributor is None
    assert outcome.unfilled_lines == [_uuid(1)]


def test_unfilled_when_no_offers():
    outcome = optimize([_line(1, offers=[])], strategy="lowest_total_price")

    assert outcome.selections[0].selected_distributor is None
    assert outcome.unfilled_lines == [_uuid(1)]


def test_est_total_cost_none_when_any_line_unfilled():
    outcome = optimize(
        [
            _line(1, offers=[_offer("D1")]),
            _line(2, offers=[]),
        ],
        strategy="lowest_total_price",
    )

    assert outcome.est_total_cost is None


def test_deterministic_across_runs():
    rows = [
        _line(3, offers=[_offer("Beta", unit_price="1.00")], risk_flags=["single_source"]),
        _line(1, offers=[_offer("Alpha", unit_price="1.00")]),
        _line(2, offers=[_offer("Alpha", unit_price="2.00"), _offer("Beta", unit_price="2.00")]),
    ]

    first = optimize(rows, strategy="preferred_first", preferred_distributors=["Beta"])
    second = optimize(
        list(reversed(rows)),
        strategy="preferred_first",
        preferred_distributors=["Beta"],
    )

    assert first == second
    assert first.selections[0].project_entry_id == _uuid(1)
    assert first.selections[2].risk_flags == ("single_source",)


def test_optimizer_outcome_serializes_decimals_as_strings():
    outcome = optimize([_line(1, offers=[_offer("D1", unit_price="1.25")])])
    payload = OptimizerOutcomeOut.model_validate(outcome).model_dump(mode="json")

    assert payload["selections"][0]["selected_unit_price"] == "1.25"
    assert payload["est_total_cost"] == "6.25"
