"""Quantity representation helpers — units-of-measure track, steps 1-2.

Migration ``0074`` widened every stock / BOM / order quantity column from
``Integer`` to ``Numeric(18, 6)`` and gave parts and ledger rows a unit
code. That migration is **a widening only**: nothing in the API accepts
or emits a fractional quantity yet, and it stays reversible right up
until the first fractional row is written (see the module docstring of
``alembic/versions/0074_uom_widen_quantities.py``).

Two consequences of the wider column need exactly one home:

* psycopg now hands back ``Decimal("5.000000")`` where it used to hand
  back ``5``. Anything that drops an ORM quantity straight into an
  untyped response dict would start emitting ``5.0`` instead of ``5``
  (FastAPI's ``jsonable_encoder`` renders a scaled ``Decimal`` as a
  float). Pydantic-typed responses are unaffected — an ``int`` field
  coerces an integral ``Decimal`` back to ``int`` — so only the untyped
  serialisers need ``quantity_out``.
* every ledger row carries its own unit stamp rather than resolving the
  unit through ``parts.unit_of_measure`` at read time. A part-level-only
  unit would let an edit retroactively reinterpret history: flip a part
  from ``pcs`` to ``m`` and 500 pieces silently become 500 metres, which
  is precisely what an append-only ledger exists to prevent.
  ``DEFAULT_UNIT`` is the only value this step ever writes.

Step 2 (this module's ``as_quantity`` / ``QUANTITY_ZERO``) makes the
*internal* plumbing exact. The read path — ``current_quantity`` and every
roll-up built on it — now carries ``Decimal`` end to end instead of
truncating each ledger sum back to ``int``. Nothing observable changes
while the API still validates integers in; what changes is that the day
fractional input opens, no intermediate step has already thrown the
fraction away. ``backend/scripts/check_quantity_coercions.py`` is the CI
guard that keeps it that way.
"""
from __future__ import annotations

from decimal import Decimal

#: Storage scale of every quantity column (``Numeric(18, 6)``) — micrometre
#: resolution on metres, microgram on grams. Matches the scale already used
#: for money throughout the schema so quantity x price stays scale-consistent.
QUANTITY_SCALE = Decimal("0.000001")

#: Unit code defaulted on parts and stamped on every ledger row. Until the
#: unit becomes user-selectable (a later step of this track) it is the only
#: value that ever reaches the database.
DEFAULT_UNIT = "pcs"

#: Column width of ``parts.unit_of_measure`` / ``stock_entries.unit``.
UNIT_CODE_MAX_LENGTH = 16

#: Neutral element for quantity arithmetic. Use this rather than a bare
#: ``0`` for accumulator seeds and ``dict.get`` defaults so a roll-up is
#: ``Decimal`` even when it summed nothing.
QUANTITY_ZERO = Decimal(0)


def as_quantity(value: Decimal | int | str | None) -> Decimal:
    """Coerce a quantity read out of the database into an exact ``Decimal``.

    ``NULL`` and a missing roll-up key both mean "no stock", so ``None``
    becomes ``QUANTITY_ZERO``. ``float`` is deliberately **not** accepted:
    a quantity that has already been through binary floating point has
    already lost the exactness this function exists to preserve, and
    silently laundering it back into a ``Decimal`` would hide that. Pass
    the ``Decimal`` the ORM handed you.

    **The storage scale is padding, not part of the value.** Postgres hands
    back ``Decimal("10.000000")`` for a ten because the column is
    ``Numeric(18, 6)``; ten is still ten. That padding is not inert —
    ``Decimal`` multiplication *adds* exponents, so a ten carrying six
    decimal places turns a ``0.500000`` unit price into a
    ``5.000000000000`` extended cost, which the money schemas then render
    verbatim as a string. Trimming here means a quantity used as a
    multiplier leaves money at money's own scale, and every value keeps
    exactly the digits it actually has.
    """
    if value is None:
        return QUANTITY_ZERO
    if isinstance(value, Decimal):
        return _trim(value)
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TypeError(f"not an exact quantity: {value!r}")
    return _trim(Decimal(value))


def _trim(value: Decimal) -> Decimal:
    """`value` without the trailing zeros the column's scale padded on.

    ``normalize()`` alone is not enough: it happily returns ``1E+1`` for a
    ten, which reads as scientific notation everywhere the value is
    stringified. Pulling a positive exponent back to zero keeps the plain
    integer form.
    """
    if not value:
        return QUANTITY_ZERO
    trimmed = value.normalize()
    if trimmed.as_tuple().exponent > 0:  # type: ignore[operator]
        trimmed = trimmed.quantize(Decimal(1))
    return trimmed


def quantity_out(value: Decimal | int | float | None) -> int | float | None:
    """Render a stored quantity for an *untyped* JSON response dict.

    ``None`` passes through. A whole value comes back as ``int`` — which,
    in this step, is every value, so the wire format is byte-identical to
    what the integer columns produced. A value that is somehow fractional
    comes back as ``float`` rather than being truncated: this helper must
    never quietly destroy a measured quantity, and a stray ``0.5`` showing
    up in a response is the loud failure mode we want.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    whole = int(dec)
    return whole if dec == whole else float(dec)
