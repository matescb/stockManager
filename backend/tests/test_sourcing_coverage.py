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
    moq: int | None = None,
    lead_time_days: int | None = 3,
    price_breaks: list[tuple[int, str]] | None = None,
) -> SourcingBomOfferOut:
    return SourcingBomOfferOut(
        mpn=mpn,
        distributor=distributor,
        stock=stock,
        unit_price=Decimal(unit_price),
        moq=moq,
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
    assert matrix.lowest_total_price_combo == []
    assert matrix.lowest_total_price_total is None
    assert matrix.fewest_distributors_combo == []
    assert matrix.fewest_distributors_total is None
    assert matrix.target_coverage_pct == 1.0


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
    assert matrix.fewest_distributors_combo == ["DigiKey"]
    assert matrix.fewest_distributors_total == Decimal("22.50")


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
    assert payload["lowest_total_price_total"] == "6.25"
    assert payload["fewest_distributors_total"] == "6.25"


def test_lowest_total_price_combo_uses_optimizer_selections():
    matrix = compute_coverage(
        [
            _line(
                1,
                offers=[
                    _offer("OneStop", unit_price="9.00"),
                    _offer("Alpha", unit_price="1.00"),
                ],
            ),
            _line(
                2,
                offers=[
                    _offer("OneStop", unit_price="9.00"),
                    _offer("Beta", unit_price="2.00"),
                ],
            ),
            _line(
                3,
                offers=[
                    _offer("OneStop", unit_price="9.00"),
                    _offer("Gamma", unit_price="3.00"),
                ],
            ),
        ]
    )

    assert matrix.lowest_total_price_combo == ["Alpha", "Beta", "Gamma"]
    assert matrix.lowest_total_price_total == Decimal("30.00")
    assert matrix.fewest_distributors_combo == ["OneStop"]
    assert matrix.fewest_distributors_total == Decimal("135.00")


def test_fewest_distributors_combo_returns_minimum_set():
    matrix = compute_coverage(
        [
            _line(1, offers=[_offer("Single", unit_price="5.00"), _offer("Alpha")]),
            _line(2, offers=[_offer("Single", unit_price="5.00"), _offer("Beta")]),
            _line(3, offers=[_offer("Single", unit_price="5.00"), _offer("Gamma")]),
        ]
    )

    assert matrix.fewest_distributors_combo == ["Single"]
    assert matrix.fewest_distributors_total == Decimal("75.00")


def test_fewest_distributors_combo_with_two_distributors_needed():
    matrix = compute_coverage(
        [
            _line(1, offers=[_offer("Alpha")]),
            _line(2, offers=[_offer("Alpha"), _offer("Beta")]),
            _line(3, offers=[_offer("Beta")]),
        ]
    )

    assert matrix.fewest_distributors_combo == ["Alpha", "Beta"]
    assert matrix.fewest_distributors_total == Decimal("15.00")


def test_fewest_distributors_combo_tiebreak_by_total_cost():
    matrix = compute_coverage(
        [
            _line(
                1,
                offers=[
                    _offer("Alpha", unit_price="1.00"),
                    _offer("Gamma", unit_price="9.00"),
                ],
            ),
            _line(
                2,
                offers=[
                    _offer("Beta", unit_price="1.00"),
                    _offer("Delta", unit_price="9.00"),
                ],
            ),
        ]
    )

    assert matrix.fewest_distributors_combo == ["Alpha", "Beta"]
    assert matrix.fewest_distributors_total == Decimal("10.00")


def test_fewest_distributors_respects_moq_stock_and_totals_selected_qty():
    matrix = compute_coverage(
        [
            _line(
                1,
                short_by=10,
                offers=[
                    _offer("BulkOnly", stock=50, unit_price="1.00", moq=100),
                    _offer("EnoughBulk", stock=100, unit_price="1.00", moq=100),
                    _offer("Eaches", stock=10, unit_price="20.00"),
                ],
            )
        ]
    )

    assert "BulkOnly" not in [row.distributor for row in matrix.rows if row.lines_covered]
    assert matrix.fewest_distributors_combo == ["EnoughBulk"]
    assert matrix.fewest_distributors_total == Decimal("100.00")


def test_greedy_threshold_30_distributors_near_optimal():
    rows = [
        _line(
            index,
            offers=[
                _offer("D00", unit_price="2.00"),
                _offer(f"D{index:02d}", unit_price="1.00"),
            ],
        )
        for index in range(1, 31)
    ]

    matrix = compute_coverage(rows)

    assert len(matrix.rows) == 31
    assert matrix.fewest_distributors_combo == ["D00"]
    assert matrix.fewest_distributors_total == Decimal("300.00")
