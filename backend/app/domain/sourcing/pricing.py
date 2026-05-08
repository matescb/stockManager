"""Pricing helpers for sourcing offers."""
from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any


def best_unit_price_at_qty(
    price_breaks: Iterable[Any],
    qty: int,
) -> tuple[Decimal, int] | None:
    """Return the applicable unit price and break quantity for `qty`.

    If `qty` is below the smallest break, the smallest break applies. If it
    is above the largest break, the largest break applies.
    """
    breaks = [
        item
        for item in (_normalise_break(item) for item in price_breaks)
        if item is not None
    ]
    breaks.sort(key=lambda item: item[0])
    if not breaks or qty < 1:
        return None

    selected = breaks[0]
    for candidate in breaks:
        if candidate[0] > qty:
            break
        selected = candidate
    quantity, unit_price = selected
    return unit_price, quantity


def extended_price(price_breaks: Iterable[Any], qty: int) -> Decimal | None:
    best = best_unit_price_at_qty(price_breaks, qty)
    if best is None:
        return None
    unit_price, _break_quantity = best
    return unit_price * Decimal(qty)


def _normalise_break(item: Any) -> tuple[int, Decimal] | None:
    if isinstance(item, dict):
        quantity = item.get("quantity")
        unit_price = item.get("unit_price")
    else:
        quantity = getattr(item, "quantity", None)
        unit_price = getattr(item, "unit_price", None)

    if quantity is None or unit_price is None:
        return None

    quantity_int = int(quantity)
    if quantity_int < 1:
        return None
    return quantity_int, Decimal(str(unit_price))
