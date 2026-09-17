"""How a `custom_fields` value goes onto the wire.

One function, its own module, because two routers now need it and a
`custom_fields` number that renders one way on `GET
/api/custom-fields/by-object/...` and another way in the parts list's
`specs` payload is a bug the frontend cannot work around: `lib/schemas.ts`
declares `value_num` a string on both.
"""
from __future__ import annotations

from decimal import Decimal

__all__ = ["value_num_out"]


def value_num_out(value: Decimal | None) -> str | None:
    """`Decimal("10000.000000000000000000")` → `"10000"`.

    Postgres hands back the column's full declared scale, so `str()` would
    put eighteen trailing zeros on every number. `normalize()` alone swings
    the other way and yields `1E+4`, so the `"f"` format is the half that
    makes it fixed-point. A string rather than a JSON number because
    `Numeric(36,18)` is exact and a JS double is not — sorting and range
    filters on this column are server-side, where the index is.
    """
    return None if value is None else format(value.normalize(), "f")
