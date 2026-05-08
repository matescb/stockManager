from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from app.domain.sourcing.coverage import compute_coverage
from app.domain.sourcing.schemas import (
    DistributorCoverageMatrixOut,
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
    stock: int = 10,
    unit_price: str = "1.00",
    lead_time_days: int | None = 3,
    price_breaks: list[tuple[int, str]] | None = None,
) -> SourcingBomOfferOut:
    return SourcingBomOfferOut(
        mpn=mpn,
        distributor=distributor,
        stock=stock,
        unit_price=Decimal(unit_price),
        lead_time_days=lead_time_days,
        price_breaks=[
            SourcingBomPriceBreakOut(quantity=quantity, unit_price=Decimal(price))
            for quantity, price in (price_breaks or [(1, unit_price)])
        ],
    )


def _line(index: int, *, short_by: int = 5, offers: list[SourcingBomOfferOut] | None = None):
    return SourcingBomLineOut(
        project_entry_id=_uuid(index),
        part_id=_uuid(index + 1000),
        part_name=f"Line {index}",
        required=short_by,
        available=0,
        substitute_available=0,
        short_by=short_by,
        authorized_stock=sum(offer.stock for offer in offers or []),
        offers=offers or [],
    )


def test_empty_bom_returns_empty_matrix():
    matrix = compute_coverage([])

    assert matrix.total_lines == 0
    assert matrix.rows == []
    assert matrix.best_single_distributor is None
    assert matrix.best_two_distributor_combo is None


def test_single_distributor_covers_all():
    matrix = compute_coverage(
        [
            _line(
                1,
                offers=[
                    _offer(
                        "DigiKey",
                        unit_price="9.99",
                        price_breaks=[(1, "2.00"), (5, "1.50")],
                    )
                ],
            ),
            _line(2, offers=[_offer("DigiKey", unit_price="3.00", lead_time_days=8)]),
        ]
    )

    assert matrix.best_single_distributor == "DigiKey"
    assert matrix.best_two_distributor_combo is None
    assert matrix.rows[0].lines_covered == 2
    assert matrix.rows[0].coverage_pct == 1
    assert matrix.rows[0].est_total_cost == Decimal("22.50")
    assert matrix.rows[0].worst_lead_time_days == 8


def test_two_distributors_needed_finds_optimal_pair():
    matrix = compute_coverage(
        [
            _line(1, offers=[_offer("D1")]),
            _line(2, offers=[_offer("D2")]),
            _line(3, offers=[_offer("D3", stock=1)]),
        ]
    )

    assert matrix.best_single_distributor == "D1"
    assert matrix.best_two_distributor_combo == ("D1", "D2")


def test_tie_breaks_by_preferred_then_cost_then_alpha():
    preferred_matrix = compute_coverage(
        [
            _line(
                1,
                offers=[
                    _offer("DigiKey", unit_price="5.00"),
                    _offer("Mouser", unit_price="1.00"),
                ],
            )
        ],
        preferred_distributors=["DigiKey", "Mouser"],
    )
    cost_matrix = compute_coverage(
        [
            _line(
                1,
                offers=[
                    _offer("Zed", unit_price="5.00"),
                    _offer("Alpha", unit_price="1.00"),
                ],
            )
        ]
    )
    alpha_matrix = compute_coverage(
        [
            _line(
                1,
                offers=[
                    _offer("Beta", unit_price="1.00"),
                    _offer("Alpha", unit_price="1.00"),
                ],
            )
        ]
    )

    assert preferred_matrix.best_single_distributor == "DigiKey"
    assert cost_matrix.best_single_distributor == "Alpha"
    assert alpha_matrix.best_single_distributor == "Alpha"


def test_greedy_fallback_for_many_distributors():
    rows = [
        _line(1, offers=[_offer("D00"), _offer("D01")]),
        _line(2, offers=[_offer("D00")]),
        _line(3, offers=[_offer("D02")]),
    ]
    for index in range(3, 60):
        rows[0].offers.append(_offer(f"D{index:02d}", stock=1))

    matrix = compute_coverage(rows)

    assert len(matrix.rows) == 60
    assert matrix.best_single_distributor == "D00"
    # Known limitation: for more than 30 distributors this is the greedy pair,
    # not an exhaustive proof of the optimal pair.
    assert matrix.best_two_distributor_combo == ("D00", "D02")


def test_lines_uncovered_lists_project_entry_ids():
    matrix = compute_coverage(
        [
            _line(1, offers=[_offer("D1")]),
            _line(2, offers=[_offer("D1", stock=4)]),
            _line(3, offers=[]),
        ]
    )

    assert matrix.rows[0].distributor == "D1"
    assert matrix.rows[0].lines_uncovered == [_uuid(2), _uuid(3)]


def test_decimal_serialisation():
    matrix = compute_coverage([_line(1, offers=[_offer("D1", unit_price="1.25")])])
    payload = DistributorCoverageMatrixOut.model_validate(matrix).model_dump(mode="json")

    assert payload["rows"][0]["est_total_cost"] == "6.25"
    assert isinstance(payload["rows"][0]["est_total_cost"], str)
