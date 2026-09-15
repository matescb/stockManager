"""Parse a provider spec value into a base-unit number and one canonical
display string.

The same quantity reaches us in a dozen spellings — `10k`, `10 kOhms`,
`1 MOhms`, `26mOhm Max`, `0.063W, 1/16W`, `±100ppm/°C`. Storing them
verbatim is why the Specs tab reads like a scrape and why nothing can be
sorted. This module is the one place that turns text into

    (value_num in the SI base unit, unit symbol, canonical display)

and it is deliberately table-driven and side-effect free: no DB, no
network, no config. `parse_si` NEVER raises — an unreadable value is
``None``, which the caller keeps verbatim rather than guessing at.

Three rules worth knowing before editing the tables:

* the full unit token is looked up before a multi-character token's SI
  prefix is peeled off, so `Hz` stays hertz instead of becoming hecto-`z`
  and `ppm` stays parts-per-million instead of pico-`pm`;
* a LONE single-character token is resolved as a prefix first, and
  case-sensitively, because `M` (mega) case-folds onto `m` (metre) —
  reading `10M` as ten metres is a silent factor of a million;
* `%`, `ppm/°C` and `°C` are NOT scalable — 0.5% must never render as
  `500 m%`, and `5 k` under a `%` key is unreadable, not 5000%.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.core.logging import get_logger

__all__ = ["ParsedValue", "format_si", "parse_si"]

_log = get_logger(__name__)


@dataclass(frozen=True)
class ParsedValue:
    """`value_num` is in the SI base unit (ohms, farads, watts…).

    It is ``None`` for a value that is real but not a single number —
    an operating-temperature range, for instance — where `display` still
    carries the normalised text.
    """

    value_num: Decimal | None
    unit: str
    display: str


# --- unit table ------------------------------------------------------------
# token (lower-cased, spaces removed) -> (symbol, scalable, space before unit)
_UNITS: dict[str, tuple[str, bool, bool]] = {
    "ohm": ("Ω", True, True),
    "ohms": ("Ω", True, True),
    # U+03A9 GREEK CAPITAL OMEGA and U+2126 OHM SIGN both lower-case to
    # U+03C9, so this one entry covers every spelling of the symbol.
    "ω": ("Ω", True, True),
    "f": ("F", True, True),
    "h": ("H", True, True),
    "v": ("V", True, True),
    "vdc": ("V", True, True),
    "vac": ("V", True, True),
    "a": ("A", True, True),
    "w": ("W", True, True),
    "hz": ("Hz", True, True),
    "s": ("s", True, True),
    "m": ("m", True, True),
    "cd": ("cd", True, True),
    "%": ("%", False, False),
    "ppm": ("ppm", False, True),
    "ppm/°c": ("ppm/°C", False, True),
    "ppm/c": ("ppm/°C", False, True),
    "ppm/k": ("ppm/°C", False, True),
    "°c": ("°C", False, False),
    # Bare `C` — `-55 C to +125 C` is a real DigiKey spelling. Safe because
    # `c` is not an SI prefix, so it can never shadow one.
    "c": ("°C", False, False),
    "degc": ("°C", False, False),
}

_UNIT_BY_SYMBOL: dict[str, tuple[str, bool, bool]] = {
    sym: (sym, scalable, space) for sym, scalable, space in _UNITS.values()
}

# Multipliers accepted on input. Case matters: `M` is mega, `m` is milli.
# Femto is deliberately absent — `f` would collide with farad.
_PREFIX_EXPONENTS: dict[str, int] = {
    "T": 12, "G": 9, "M": 6, "k": 3, "K": 3,
    "m": -3, "u": -6, "µ": -6, "μ": -6, "n": -9, "p": -12,
}

# Multipliers used on output — one canonical spelling per decade triple.
_DISPLAY_PREFIXES: dict[int, str] = {
    12: "T", 9: "G", 6: "M", 3: "k", 0: "",
    -3: "m", -6: "µ", -9: "n", -12: "p",
}

_NUMBER_RE = re.compile(r"^([+-]?\d+/\d+|[+-]?\d+(?:\.\d+)?)\s*(.*)$", re.DOTALL)
_PLUS_MINUS_RE = re.compile(r"^(?:±|\+/-|\+-)\s*")
_QUALIFIER_RE = re.compile(
    r"\s*\(?\b(?:max|min|typ|typical|nom|nominal|abs|ref|ta|tj|tc)\b\.?\)?\s*$",
    re.IGNORECASE,
)
_RANGE_SPLIT_RE = re.compile(r"\s*(?:~|\.\.\.|…|\bto\b)\s*", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")
# `1,000 mA` is one number; `0.063W, 1/16W` is two values. Strip the
# thousands grouping first, then split only on a comma that is NOT
# immediately followed by a digit — so a European decimal comma (`4,7 uF`)
# is refused outright instead of silently read as `4`.
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(?:\D|$))")
_VALUE_SPLIT_RE = re.compile(r",(?!\d)")

_DASHES = {"-", "–", "—", "--"}
_MAX_INPUT_CHARS = 200
_DISPLAY_PLACES = Decimal("1.0000")

# `custom_fields.value_num` is NUMERIC(36,18): 18 integer and 18 fractional
# digits. A value outside that is silently rounded (or refused) by Postgres,
# so it is bounded here instead — and nothing in electronics lives beyond an
# exaohm or below an attofarad, so a text that parses to one is garbage, not
# a spec. `1/3 W` is rounded to the column's scale for the same reason.
_VALUE_NUM_QUANTUM = Decimal("1E-18")
_VALUE_NUM_MAX_EXPONENT = 17  # |value| < 1e18
_VALUE_NUM_MIN_EXPONENT = -18


def parse_si(text: str | None, *, unit_hint: str | None = None) -> ParsedValue | None:
    """Best-effort parse of one provider spec value. Never raises.

    `unit_hint` is the canonical unit symbol the schema expects for this
    key (`"Ω"`, `"F"`, …). It only fills in a unit the text omits — a
    unit spelled out in the text always wins — which is what lets a bare
    `10k` under `resistance` become `10 kΩ`.
    """
    try:
        return _parse(text, unit_hint)
    except Exception:  # noqa: BLE001 - a parser refusal must never propagate
        # Logged, not swallowed silently: the contract is "return None", but
        # an exception here is a bug in this module, not a bad input.
        _log.debug("spec value could not be parsed: %r", text, exc_info=True)
        return None


def format_si(value: Decimal, unit: str) -> str:
    """Render *value* (already in the base unit) in engineering notation."""
    symbol, scalable, space = _UNIT_BY_SYMBOL.get(unit, (unit, False, True))
    if not value.is_finite():
        # NaN / Infinity round-tripped from a NUMERIC column. Say so rather
        # than rendering a confident "0 Ω".
        return _join(str(value), symbol, space)
    if not scalable or value == 0:
        return _join(_trim(value), symbol, space)
    try:
        return _join(*_engineering(value, symbol), space)
    except ArithmeticError:
        # Exponent beyond Decimal's range — nothing real, but don't raise.
        return _join(_trim(value), symbol, space)


def _engineering(value: Decimal, symbol: str) -> tuple[str, str]:
    exponent = _clamp_exponent(value.adjusted())
    mantissa = _trim(value.scaleb(-exponent))
    # `999.99999 Ω` trims to "1000", which belongs one decade up.
    if abs(Decimal(mantissa)) >= 1000 and exponent < max(_DISPLAY_PREFIXES):
        exponent += 3
        mantissa = _trim(value.scaleb(-exponent))
    return mantissa, f"{_DISPLAY_PREFIXES[exponent]}{symbol}"


def _clamp_exponent(adjusted: int) -> int:
    return max(min(_DISPLAY_PREFIXES), min(max(_DISPLAY_PREFIXES), (adjusted // 3) * 3))


def _join(number: str, symbol: str, space: bool) -> str:
    return f"{number} {symbol}".strip() if space else f"{number}{symbol}"


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------
def _parse(text: str | None, unit_hint: str | None) -> ParsedValue | None:
    if not text:
        return None
    body = _WHITESPACE_RE.sub(" ", text).strip()
    if not body or body in _DASHES or len(body) > _MAX_INPUT_CHARS:
        return None

    body = _THOUSANDS_RE.sub("", body)
    ranged = _parse_range(body, unit_hint)
    if ranged is not None:
        return ranged

    # Multi-valued ("0.063W, 1/16W") and conditioned ("1.8 A @ 100 kHz")
    # values: the leading term is the one the schema is asking about.
    head = _VALUE_SPLIT_RE.split(body)[0].split("@")[0].strip()
    head = _PLUS_MINUS_RE.sub("", head).strip()
    while True:
        stripped = _QUALIFIER_RE.sub("", head).strip()
        if stripped == head:
            break
        head = stripped
    if not head:
        return None

    match = _NUMBER_RE.match(head)
    if match is None:
        return None
    number = _to_decimal(match.group(1))
    if number is None:
        return None

    resolved = _resolve_unit(match.group(2).strip(), unit_hint)
    if resolved is None:
        return None
    symbol, exponent = resolved
    value = _fit_to_column(number.scaleb(exponent))
    if value is None:
        return None
    return ParsedValue(value_num=value, unit=symbol, display=format_si(value, symbol))


def _fit_to_column(value: Decimal) -> Decimal | None:
    """Round to `value_num`'s scale, or refuse a magnitude it cannot hold."""
    if value == 0:
        return value
    if not _VALUE_NUM_MIN_EXPONENT <= value.adjusted() <= _VALUE_NUM_MAX_EXPONENT:
        return None
    if -int(value.as_tuple().exponent) > -_VALUE_NUM_MIN_EXPONENT:
        return value.quantize(_VALUE_NUM_QUANTUM, rounding=ROUND_HALF_UP)
    return value


def _parse_range(body: str, unit_hint: str | None) -> ParsedValue | None:
    """`-55°C ~ 125°C` → one display string and no number."""
    parts = _RANGE_SPLIT_RE.split(body)
    if len(parts) != 2 or not all(p.strip() for p in parts):
        return None
    low = _parse(parts[0], unit_hint)
    high = _parse(parts[1], unit_hint)
    if low is None or high is None:
        return None
    return ParsedValue(
        value_num=None,
        unit=low.unit or high.unit,
        display=f"{low.display} ~ {high.display}",
    )


def _to_decimal(token: str) -> Decimal | None:
    try:
        if "/" in token:
            numerator, denominator = token.split("/", 1)
            divisor = Decimal(denominator)
            if divisor == 0:
                return None
            return Decimal(numerator) / divisor
        value = Decimal(token)
    except (InvalidOperation, ArithmeticError, ValueError):
        return None
    return value if value.is_finite() else None


def _resolve_unit(rest: str, unit_hint: str | None) -> tuple[str, int] | None:
    """`"kOhms"` → `("Ω", 3)`; `""` + a hint → `(hint, 0)`."""
    token = rest.replace(" ", "")
    if not token:
        if not unit_hint:
            return None
        return (unit_hint, 0)
    # A lone SI prefix (`10k` under `resistance` is 10 kΩ) is resolved BEFORE
    # the unit table and case-SENSITIVELY. `M` (mega) case-folds onto `m`
    # (metre), so a case-insensitive whole-token lookup reads `10M` as ten
    # metres — a silent factor of a million under every resistance key.
    # `token == unit_hint` is the opposite case: `625 m` under a metre key is
    # 625 metres, not 625 milli-metres.
    if len(token) == 1 and token in _PREFIX_EXPONENTS and token != unit_hint:
        if unit_hint and _is_scalable(unit_hint):
            return (unit_hint, _PREFIX_EXPONENTS[token])
        if unit_hint:
            # `%`, `°C` and `ppm/°C` never scale: `5 k` under `tolerance` is
            # not 5000%, it is unreadable.
            return None
        if token not in _UNITS:
            # `10M`, `10k` with no unit anywhere. Not a quantity. The
            # lookup is exact, not case-folded: lower-case `m` is the metre
            # entry and falls through, upper-case `M` is only ever mega.
            return None
    # Whole-token next: `Hz` is hertz, not hecto-z.
    direct = _UNITS.get(token.lower())
    if direct is not None:
        return (direct[0], 0)
    exponent = _PREFIX_EXPONENTS.get(token[0])
    if exponent is None or len(token) == 1:
        return None
    prefixed = _UNITS.get(token[1:].lower())
    if prefixed is None:
        return None
    return (prefixed[0], exponent)


def _is_scalable(symbol: str) -> bool:
    return _UNIT_BY_SYMBOL.get(symbol, (symbol, False, True))[1]


def _trim(value: Decimal) -> str:
    """Fixed-point, no trailing zeros, at most four decimal places."""
    if not value.is_finite():
        return str(value)
    if value == 0:
        return "0"  # never "-0"
    if -int(value.as_tuple().exponent) > 4:
        value = value.quantize(_DISPLAY_PLACES, rounding=ROUND_HALF_UP)
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
