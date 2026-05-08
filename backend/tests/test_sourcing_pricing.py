from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.sourcing.pricing import best_unit_price_at_qty, extended_price


@pytest.mark.parametrize(
    ("breaks", "qty", "expected"),
    [
        ([], 10, None),
        ([{"quantity": 10, "unit_price": "1.25"}], 5, (Decimal("1.25"), 10)),
        (
            [
                {"quantity": 1, "unit_price": "2.00"},
                {"quantity": 10, "unit_price": "1.50"},
            ],
            10,
            (Decimal("1.50"), 10),
        ),
        (
            [
                {"quantity": 1, "unit_price": "2.00"},
                {"quantity": 10, "unit_price": "1.50"},
            ],
            99,
            (Decimal("1.50"), 10),
        ),
    ],
)
def test_best_unit_price_at_qty(breaks, qty, expected):
    assert best_unit_price_at_qty(breaks, qty) == expected


@pytest.mark.parametrize(
    ("breaks", "qty", "expected"),
    [
        ([], 10, None),
        ([{"quantity": 10, "unit_price": "1.25"}], 5, Decimal("6.25")),
        (
            [
                {"quantity": 1, "unit_price": "2.00"},
                {"quantity": 10, "unit_price": "1.50"},
            ],
            10,
            Decimal("15.00"),
        ),
        (
            [
                {"quantity": 1, "unit_price": "2.00"},
                {"quantity": 10, "unit_price": "1.50"},
            ],
            99,
            Decimal("148.50"),
        ),
    ],
)
def test_extended_price(breaks, qty, expected):
    assert extended_price(breaks, qty) == expected
