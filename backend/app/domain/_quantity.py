"""Quantity representation helpers — units-of-measure track, step 1.

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
