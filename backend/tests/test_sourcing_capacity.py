from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from app.domain.sourcing.coverage import _total_currency, compute_build_capacity
from app.domain.sourcing.schemas import (
    BuildCapacityOut,
    SourcingBomLineOut,
    SourcingBomOfferOut,
    SourcingBomPriceBreakOut,
)


def _uuid(index: int) -> UUID:
    return UUID(f"00000000-0000-0000-0000-{index:012d}")


def _offer(
    *,
    stock: int = 100,
    unit_price: str | Decimal = "1.00",
    distributor: str = "DigiKey",
    currency: str | None = None,
    unit_price_converted: str | None = None,
    currency_displayed: str | None = None,
    fx_converted: bool | None = None,
) -> SourcingBomOfferOut:
    return SourcingBomOfferOut(
        mpn="MPN",
        distributor=distributor,
        stock=stock,
        unit_price=Decimal(unit_price),
        currency=currency,
        unit_price_converted=(
            Decimal(unit_price_converted) if unit_price_converted is not None else None
        ),
        currency_displayed=currency_displayed,
        fx_converted=fx_converted,
        price_breaks=[
            SourcingBomPriceBreakOut(quantity=1, unit_price=Decimal(unit_price))
        ],
    )


def _line(
    index: int,
    *,
    required: int = 100,
    available: int = 0,
    substitute_available: int = 0,
    authorized_stock: int = 0,
    best_offer: SourcingBomOfferOut | None = None,
) -> SourcingBomLineOut:
    return SourcingBomLineOut(
        project_entry_id=_uuid(index),
        part_id=_uuid(index + 1000),
        part_name=f"Line {index}",
        required=required,
        available=available,
        substitute_available=substitute_available,
        short_by=max(0, required - available - substitute_available),
        authorized_stock=authorized_stock,
        offers=[best_offer] if best_offer is not None else [],
        best_offer=best_offer,
    )


def test_empty_bom():
    capacity = compute_build_capacity([], requested_build_quantity=100)

    assert capacity.can_build_now == 0
    assert capacity.can_build_after_purchase == 0
    assert capacity.total_bom_cost is None
    assert capacity.purchase_to_pay_cost is None
    assert capacity.est_purchase_cost is None
    assert capacity.blocking_lines_now == []
    assert capacity.blocking_lines_after_purchase == []


def test_fully_stocked_internal():
    capacity = compute_build_capacity(
        [
            _line(1, required=100, available=100),
            _line(2, required=200, available=200),
        ],
        requested_build_quantity=100,
    )

    assert capacity.can_build_now == 100
    assert capacity.can_build_after_purchase == 100
    assert capacity.total_bom_cost is None
    assert capacity.purchase_to_pay_cost == Decimal("0")
    assert capacity.est_purchase_cost == Decimal("0")


def test_partially_stocked_with_authorized_supply():
    capacity = compute_build_capacity(
        [
            _line(
                1,
                required=100,
                available=50,
                authorized_stock=200,
                best_offer=_offer(stock=200, unit_price="0.25"),
            ),
            _line(2, required=100, available=100),
        ],
        requested_build_quantity=100,
    )

    assert capacity.can_build_now == 50
    assert capacity.can_build_after_purchase == 100
    assert capacity.total_bom_cost == Decimal("25.00")
    assert capacity.purchase_to_pay_cost == Decimal("12.50")
    assert capacity.est_purchase_cost == Decimal("12.50")
    assert capacity.blocking_lines_now == [_uuid(1)]


def test_authorized_supply_zero_blocks_after_purchase():
    capacity = compute_build_capacity(
        [
            _line(1, required=100, available=50),
            _line(2, required=100, available=100),
        ],
        requested_build_quantity=100,
    )

    assert capacity.can_build_after_purchase == 50
    assert capacity.blocking_lines_after_purchase == [_uuid(1)]
    assert capacity.purchase_to_pay_cost is None


def test_purchase_to_pay_cost_excludes_blocking_lines():
    capacity = compute_build_capacity(
        [
            _line(1, required=100, available=50),
            _line(
                2,
                required=100,
                available=75,
                authorized_stock=100,
                best_offer=_offer(stock=100, unit_price="1.00"),
            ),
        ],
        requested_build_quantity=100,
    )

    assert capacity.can_build_after_purchase == 50
    assert capacity.blocking_lines_after_purchase == [_uuid(1)]
    assert capacity.purchase_to_pay_cost == Decimal("25.00")
    assert capacity.est_purchase_cost == Decimal("25.00")


def test_est_purchase_cost_correct_at_build_quantity_1():
    capacity = compute_build_capacity(
        [
            _line(
                1,
                required=1,
                available=0,
                best_offer=_offer(stock=100, unit_price="2.50"),
            )
        ],
        requested_build_quantity=1,
    )

    assert capacity.can_build_now == 0
    assert capacity.can_build_after_purchase == 0
    assert capacity.purchase_to_pay_cost == Decimal("2.50")
    assert capacity.est_purchase_cost == Decimal("2.50")


def test_est_purchase_cost_priced_when_capacity_floors_to_zero():
    """Regression for CLAUDE.md hard rule: Sourcing est_purchase_cost still
    prices when after-purchase capacity floors to zero.
    """
    capacity = compute_build_capacity(
        [
            _line(
                1,
                required=3,
                available=2,
                best_offer=_offer(
                    stock=100,
                    unit_price=Decimal("5.00"),
                    currency="EUR",
                ),
            )
        ],
        requested_build_quantity=1,
    )

    assert capacity.can_build_now == 0
    assert capacity.can_build_after_purchase == 0
    assert capacity.est_purchase_cost is not None
    assert capacity.est_purchase_cost > Decimal("0")
    assert capacity.purchase_to_pay_cost == Decimal("5.00")
    assert capacity.est_purchase_cost == Decimal("5.00")


def test_est_purchase_cost_correct_at_build_quantity_2():
    capacity = compute_build_capacity(
        [
            _line(
                1,
                required=2,
                available=0,
                best_offer=_offer(stock=100, unit_price="2.50"),
            )
        ],
        requested_build_quantity=2,
    )

    assert capacity.can_build_now == 0
    assert capacity.can_build_after_purchase == 0
    assert capacity.purchase_to_pay_cost == Decimal("5.00")
    assert capacity.est_purchase_cost == Decimal("5.00")


def test_est_purchase_cost_unchanged_at_build_quantity_100():
    capacity = compute_build_capacity(
        [
            _line(
                1,
                required=100,
                available=0,
                authorized_stock=100,
                best_offer=_offer(stock=100, unit_price="2.50"),
            )
        ],
        requested_build_quantity=100,
    )

    assert capacity.can_build_now == 0
    assert capacity.can_build_after_purchase == 100
    assert capacity.total_bom_cost == Decimal("250.00")
    assert capacity.purchase_to_pay_cost == Decimal("250.00")
    assert capacity.est_purchase_cost == Decimal("250.00")


def test_blocking_lines_lists_binding_constraints():
    capacity = compute_build_capacity(
        [
            _line(1, required=100, available=50, authorized_stock=25),
            _line(2, required=200, available=100, authorized_stock=50),
            _line(3, required=100, available=80, authorized_stock=100),
        ],
        requested_build_quantity=100,
    )

    assert capacity.can_build_now == 50
    assert capacity.can_build_after_purchase == 75
    assert capacity.blocking_lines_now == [_uuid(1), _uuid(2)]
    assert capacity.blocking_lines_after_purchase == [_uuid(1), _uuid(2)]


def test_floor_not_round():
    capacity = compute_build_capacity(
        [_line(1, required=100, available=49)],
        requested_build_quantity=10,
    )

    assert capacity.can_build_now == 4
    assert capacity.can_build_after_purchase == 4


def test_required_zero_line_ignored():
    capacity = compute_build_capacity(
        [
            _line(1, required=0, available=0),
            _line(2, required=100, available=100),
        ],
        requested_build_quantity=100,
    )

    assert capacity.can_build_now == 100
    assert capacity.purchase_to_pay_cost == Decimal("0")
    assert capacity.blocking_lines_now == [_uuid(2)]


def test_total_bom_cost_sums_required_quantity_unit_price():
    capacity = compute_build_capacity(
        [
            _line(1, required=2, best_offer=_offer(unit_price="1.50")),
            _line(2, required=3, best_offer=_offer(unit_price="2.00")),
        ],
        requested_build_quantity=1,
    )

    assert capacity.total_bom_cost == Decimal("9.00")


def test_cost_per_single_bom_divides_total_by_build_quantity():
    capacity = compute_build_capacity(
        [
            _line(1, required=400, best_offer=_offer(unit_price="2.50")),
        ],
        requested_build_quantity=4,
    )

    assert capacity.total_bom_cost == Decimal("1000.00")
    assert capacity.cost_per_single_bom == Decimal("250.00")


def test_cost_per_single_bom_none_when_total_bom_cost_none():
    capacity = compute_build_capacity(
        [
            _line(1, required=10),
            _line(2, required=5),
        ],
        requested_build_quantity=3,
    )

    assert capacity.total_bom_cost is None
    assert capacity.cost_per_single_bom is None


def test_cost_per_single_bom_none_when_build_quantity_zero():
    capacity = compute_build_capacity(
        [
            _line(1, required=10, best_offer=_offer(unit_price="2.00")),
        ],
        requested_build_quantity=0,
    )

    assert capacity.total_bom_cost == Decimal("20.00")
    assert capacity.cost_per_single_bom is None


def test_cost_per_single_bom_decimal_precision():
    capacity = compute_build_capacity(
        [
            _line(1, required=1, best_offer=_offer(unit_price="333.33")),
        ],
        requested_build_quantity=3,
    )

    assert capacity.total_bom_cost == Decimal("333.33")
    assert capacity.cost_per_single_bom == Decimal("111.11")


def test_total_bom_cost_ignores_stock_on_hand():
    capacity = compute_build_capacity(
        [
            _line(1, required=10, available=25, best_offer=_offer(unit_price="0.25")),
            _line(2, required=5, available=10, best_offer=_offer(unit_price="2.00")),
        ],
        requested_build_quantity=1,
    )

    assert capacity.total_bom_cost == Decimal("12.50")


def test_total_bom_cost_skips_unpriced_lines():
    capacity = compute_build_capacity(
        [
            _line(1, required=10, best_offer=_offer(unit_price="0.25")),
            _line(2, required=5),
        ],
        requested_build_quantity=1,
    )

    assert capacity.total_bom_cost == Decimal("2.50")


def test_total_bom_cost_none_when_no_line_has_pricing():
    capacity = compute_build_capacity(
        [
            _line(1, required=10),
            _line(2, required=5),
        ],
        requested_build_quantity=1,
    )

    assert capacity.total_bom_cost is None


def test_total_currency_returns_none_when_mixed():
    assert _total_currency(
        [
            _line(1, best_offer=_offer(currency="USD")),
            _line(2, best_offer=_offer(currency="EUR")),
        ]
    ) is None
    assert _total_currency(
        [
            _line(
                1,
                best_offer=_offer(
                    currency="USD",
                    unit_price_converted="1.00",
                    currency_displayed="EUR",
                ),
            ),
            _line(
                2,
                best_offer=_offer(
                    currency="USD",
                    unit_price_converted="1.00",
                    currency_displayed="GBP",
                ),
            ),
        ]
    ) is None


def test_total_currency_returns_first_when_all_same():
    assert _total_currency(
        [
            _line(1, best_offer=_offer(currency="usd")),
            _line(2, best_offer=_offer(currency="USD")),
        ]
    ) == "USD"


def test_total_currency_handles_empty_and_none():
    assert _total_currency([]) is None
    assert _total_currency([_line(1)]) is None


def test_compute_build_capacity_currency_is_none_when_offers_mix_currencies():
    capacity = compute_build_capacity(
        [
            _line(1, required=2, best_offer=_offer(unit_price="1.50", currency="USD")),
            _line(2, required=3, best_offer=_offer(unit_price="2.00", currency="EUR")),
        ],
        requested_build_quantity=1,
    )

    assert capacity.total_bom_cost is None
    assert capacity.cost_per_single_bom is None
    assert capacity.purchase_to_pay_cost is None


def test_compute_build_capacity_keeps_total_when_offers_share_currency():
    capacity = compute_build_capacity(
        [
            _line(1, required=2, best_offer=_offer(unit_price="1.50", currency="USD")),
            _line(2, required=3, best_offer=_offer(unit_price="2.00", currency="USD")),
        ],
        requested_build_quantity=1,
    )

    assert capacity.total_bom_cost == Decimal("9.00")
    assert capacity.cost_per_single_bom == Decimal("9.00")
    assert capacity.purchase_to_pay_cost == Decimal("9.00")


def test_purchase_to_pay_cost_sums_short_by_unit_price():
    capacity = compute_build_capacity(
        [
            _line(1, required=100, available=0),
            _line(2, required=100, available=50, best_offer=_offer(unit_price="2.00")),
            _line(3, required=100, available=75, best_offer=_offer(unit_price="1.00")),
        ],
        requested_build_quantity=100,
    )

    assert capacity.blocking_lines_after_purchase == [_uuid(1)]
    assert capacity.purchase_to_pay_cost == Decimal("125.00")


def test_purchase_to_pay_cost_none_when_no_non_blocking_line_has_pricing():
    capacity = compute_build_capacity(
        [
            _line(1, required=100, available=0),
            _line(2, required=100, available=50),
        ],
        requested_build_quantity=100,
    )

    assert capacity.blocking_lines_after_purchase == [_uuid(1)]
    assert capacity.purchase_to_pay_cost is None


def test_est_purchase_cost_alias_equals_purchase_to_pay_cost():
    capacity = compute_build_capacity(
        [
            _line(1, required=100, available=0),
            _line(2, required=100, available=50, best_offer=_offer(unit_price="2.00")),
        ],
        requested_build_quantity=100,
    )

    assert capacity.est_purchase_cost == capacity.purchase_to_pay_cost


def test_cost_totals_skip_incompatible_display_currency_rows():
    capacity = compute_build_capacity(
        [
            _line(
                1,
                required=10,
                best_offer=_offer(
                    unit_price="2.00",
                    currency="USD",
                    unit_price_converted="1.50",
                    currency_displayed="EUR",
                    fx_converted=True,
                ),
            ),
            _line(
                2,
                required=10,
                best_offer=_offer(
                    unit_price="3.00",
                    currency="USD",
                    currency_displayed="USD",
                ),
            ),
        ],
        requested_build_quantity=1,
    )

    assert capacity.total_bom_cost == Decimal("15.00")
    assert capacity.purchase_to_pay_cost == Decimal("15.00")


def test_decimal_serialisation_for_both_costs():
    capacity = compute_build_capacity(
        [
            _line(
                1,
                required=100,
                available=50,
                authorized_stock=50,
                best_offer=_offer(stock=50, unit_price="1.25"),
            )
        ],
        requested_build_quantity=100,
    )

    payload = BuildCapacityOut.model_validate(capacity).model_dump(mode="json")

    assert payload["total_bom_cost"] == "125.00"
    assert isinstance(payload["total_bom_cost"], str)
    assert payload["cost_per_single_bom"] == "1.25"
    assert isinstance(payload["cost_per_single_bom"], str)
    assert payload["purchase_to_pay_cost"] == "62.50"
    assert isinstance(payload["purchase_to_pay_cost"], str)
    assert payload["est_purchase_cost"] == "62.50"
    assert isinstance(payload["est_purchase_cost"], str)
