"""Does every symbol pin land on a footprint pad, and every pad on a pin?

SM-008. A vendor zip's symbol and footprint come from different
generators, and a pin-number mismatch reaches the board as an open or a
short that no schematic check can see. This module answers the question
from the two files alone; `pin_pad_service.py` finds the files.

Pure and read-only: bytes in, a `PinPadResult` out. Nothing is stored —
the verdict is computed on demand from the current files, so it can
never be stale, and it carries the two sha256s it was computed on.

The rules
---------

Pins (symbol side):

* Pin numbers are compared as strings, trimmed, case-sensitive — BGA
  `A1` and `a1` are different balls. A pin with an empty number is
  ignored: it cannot be wired to any pad and KiCad never nets it.
* A multi-unit symbol is checked as the UNION of its units' pins. The
  same number in two units (an op-amp's supply pin repeated per unit)
  is one pin.
* De Morgan: body style 1 is checked. If body style 2 exists and
  carries a different set of pin numbers, the symbol is
  `not-checkable` — which style is the board's is not knowable here.
* A pin of type `no_connect` with no pad is reported under
  `no_connect_pins_without_pad` as information, not as a failure: the
  pin is declared as unconnected, and the footprint omitting it is the
  usual way packages with NC positions are drawn.
* The same number twice inside ONE unit (unit 0 counts as part of every
  unit) is a `duplicate_pin_numbers` finding — two symbol pins on one
  pad is a short. Excused, because KiCad allows them: stacked pins
  (every occurrence at the same position), and power pins of which at
  most one is visible (hidden duplicate power pins).
* A symbol that `extends` another stores no pins of its own; the parent
  is not here, so it is `not-checkable`.

Pads (footprint side) — only ELECTRICAL pads take part:

* electrical = a non-empty number, type `smd`, `thru_hole` or
  `connect`, and at least one copper layer (`*.Cu`, `F.Cu`, `In1.Cu`…);
* so an unnumbered pad, an `np_thru_hole` (NPTH / mechanical) pad, and
  a pad only on paste or mask layers never fail anything;
* several pads with one number (an exposed pad plus its thermal vias,
  or an EP numbered like a ground pin) are one pad number and legal.

Verdict: `fail` when any pin (other than `no_connect`) has no pad, any
electrical pad has no pin, or a duplicate pin number stands; else
`pass`.
"""
from __future__ import annotations

import re
import threading
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from app.domain.eda import sexpr

PASS = "pass"
FAIL = "fail"
NOT_CHECKABLE = "not-checkable"

# `not-checkable` reasons. Stable strings: agents branch on them.
REASON_SYMBOL_MISSING = "symbol_missing"
REASON_FOOTPRINT_MISSING = "footprint_missing"
REASON_SYMBOL_EXTERNAL = "symbol_external"
REASON_FOOTPRINT_EXTERNAL = "footprint_external"
REASON_SYMBOL_UNREADABLE = "symbol_unreadable"
REASON_FOOTPRINT_UNREADABLE = "footprint_unreadable"
REASON_SYMBOL_DERIVED = "symbol_derived"
REASON_BODY_STYLES_DIFFER = "body_styles_differ"

_ELECTRICAL_PAD_TYPES = frozenset({"smd", "thru_hole", "connect"})
_POWER_PIN_TYPES = frozenset({"power_in", "power_out"})
_NO_CONNECT = "no_connect"
# `NAME_<unit>_<style>` — KiCad's sub-symbol naming.
_UNIT_SUFFIX = re.compile(r"_(\d+)_(\d+)$")
_CACHE_SIZE = 1024


class Unreadable(ValueError):
    """The file does not parse as the KiCad document it claims to be."""


class NotCheckable(ValueError):
    """The file parses, but the check cannot be answered from it."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Pin:
    number: str
    type: str
    unit: int
    style: int
    at: tuple[str, str]
    hidden: bool


@dataclass(frozen=True)
class PinPadResult:
    verdict: str
    pins_without_pad: tuple[str, ...] = ()
    pads_without_pin: tuple[str, ...] = ()
    duplicate_pin_numbers: tuple[str, ...] = ()
    no_connect_pins_without_pad: tuple[str, ...] = ()
    reason: str | None = None
    detail: str | None = None
    counts: dict[str, int] = field(default_factory=dict)

    def findings(self) -> dict[str, list[str]]:
        return {
            "pins_without_pad": list(self.pins_without_pad),
            "pads_without_pin": list(self.pads_without_pin),
            "duplicate_pin_numbers": list(self.duplicate_pin_numbers),
            "no_connect_pins_without_pad": list(self.no_connect_pins_without_pad),
        }


def natural_key(number: str) -> tuple:
    """`2` before `10`, `A2` before `A10`; mixed runs compare safely."""
    return tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part)
        for part in re.split(r"(\d+)", number)
        if part
    )


def _sorted(numbers) -> tuple[str, ...]:
    return tuple(sorted(numbers, key=natural_key))


def _child(node: list, name: str) -> list | None:
    return next(
        (c for c in node if isinstance(c, list) and sexpr.head(c) == name), None
    )


# ---------------------------------------------------------------------
# Symbol
# ---------------------------------------------------------------------


def _is_hidden(pin: list) -> bool:
    if any(isinstance(c, str) and not isinstance(c, sexpr.Quoted) and c == "hide" for c in pin):
        return True
    hide = _child(pin, "hide")  # KiCad 8: `(hide yes)`
    return hide is not None and (len(hide) < 2 or str(hide[1]) == "yes")


def _pin(node: list, unit: int, style: int) -> Pin | None:
    number = _child(node, "number")
    if number is None or len(number) < 2:
        return None
    text = str(number[1]).strip()
    if not text:
        return None
    at = _child(node, "at")
    pos = (str(at[1]), str(at[2])) if at is not None and len(at) >= 3 else ("", "")
    kind = str(node[1]) if len(node) > 1 and isinstance(node[1], str) else ""
    return Pin(text, kind, unit, style, pos, _is_hidden(node))


def _unit_of(name: str) -> tuple[int, int]:
    match = _UNIT_SUFFIX.search(name)
    return (int(match[1]), int(match[2])) if match else (0, 0)


def symbol_pins(node: list) -> list[Pin]:
    """Every numbered pin of a `(symbol …)` entry, with unit and style."""
    if _child(node, "extends") is not None:
        raise NotCheckable(
            REASON_SYMBOL_DERIVED,
            "symbol extends another symbol whose pins are not stored here",
        )
    pins: list[Pin] = []
    for child in node:
        if not isinstance(child, list):
            continue
        head = sexpr.head(child)
        if head == "pin":
            pin = _pin(child, 0, 0)
            pins += [pin] if pin else []
        elif head == sexpr.SYMBOL_ROOT and len(child) > 1:
            unit, style = _unit_of(str(child[1]))
            for grand in child:
                if isinstance(grand, list) and sexpr.head(grand) == "pin":
                    pin = _pin(grand, unit, style)
                    pins += [pin] if pin else []
    return pins


def _in_style(pins: list[Pin], style: int) -> list[Pin]:
    return [p for p in pins if p.style in (0, style)]


def _duplicates(pins: list[Pin]) -> set[str]:
    """Numbers repeated within one unit, minus what KiCad allows."""
    units = {p.unit for p in pins if p.unit != 0} or {0}
    styles = {p.style for p in pins if p.style != 0} or {0}
    found: set[str] = set()
    for unit in units:
        for style in styles:
            by_number: dict[str, list[Pin]] = defaultdict(list)
            for p in pins:
                if p.unit in (0, unit) and p.style in (0, style):
                    by_number[p.number].append(p)
            found |= {n for n, group in by_number.items() if not _excused(group)}
    return found


def _excused(group: list[Pin]) -> bool:
    if len(group) < 2:
        return True
    if len({p.at for p in group}) == 1:  # stacked
        return True
    power = all(p.type in _POWER_PIN_TYPES for p in group)
    return power and sum(not p.hidden for p in group) <= 1


# ---------------------------------------------------------------------
# Footprint
# ---------------------------------------------------------------------


def _is_copper(layer: str) -> bool:
    return layer.endswith(".Cu")


def electrical_pads(node: list) -> set[str]:
    """Pad numbers of a `(footprint …)` that carry a net."""
    numbers: set[str] = set()
    for child in node:
        if not isinstance(child, list) or sexpr.head(child) != "pad" or len(child) < 3:
            continue
        number = str(child[1]).strip()
        if not number or str(child[2]) not in _ELECTRICAL_PAD_TYPES:
            continue
        layers = _child(child, "layers")
        if layers is not None and any(_is_copper(str(x)) for x in layers[1:]):
            numbers.add(number)
    return numbers


# ---------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------


def _parse_symbol(raw: bytes) -> list:
    try:
        found = sexpr.entries(raw.decode("utf-8"))
    except (UnicodeDecodeError, sexpr.SexprError) as exc:
        raise Unreadable(f"symbol file does not parse: {exc}") from exc
    if len(found) != 1:
        raise Unreadable(f"symbol file holds {len(found)} entries, expected 1")
    return found[0][1]


def _parse_footprint(raw: bytes) -> list:
    try:
        node = sexpr.parse(raw.decode("utf-8"))
    except (UnicodeDecodeError, sexpr.SexprError) as exc:
        raise Unreadable(f"footprint file does not parse: {exc}") from exc
    if sexpr.head(node) not in sexpr.FOOTPRINT_ROOTS:
        raise Unreadable("footprint file is not a (footprint …) document")
    return node


def compare(pins: list[Pin], pads: set[str]) -> PinPadResult:
    """The verdict for parsed pins and electrical pad numbers."""
    primary = _in_style(pins, 1)
    alternate = [p for p in pins if p.style == 2]
    if alternate and {p.number for p in primary} != {
        p.number for p in _in_style(pins, 2)
    }:
        return PinPadResult(
            verdict=NOT_CHECKABLE,
            reason=REASON_BODY_STYLES_DIFFER,
            detail="the symbol's two body styles number their pins differently",
        )
    numbers = {p.number for p in primary}
    no_connect = {
        n for n in numbers
        if all(p.type == _NO_CONNECT for p in primary if p.number == n)
    }
    missing = numbers - pads
    unused = pads - numbers
    dupes = _duplicates(pins)
    failing = (missing - no_connect) or unused or dupes
    return PinPadResult(
        verdict=FAIL if failing else PASS,
        pins_without_pad=_sorted(missing - no_connect),
        pads_without_pin=_sorted(unused),
        duplicate_pin_numbers=_sorted(dupes),
        no_connect_pins_without_pad=_sorted(missing & no_connect),
        counts={"pins": len(numbers), "pads": len(pads)},
    )


def check_bytes(symbol: bytes, footprint: bytes) -> PinPadResult:
    """Check one symbol file against one footprint file."""
    try:
        pins = symbol_pins(_parse_symbol(symbol))
    except NotCheckable as exc:
        return PinPadResult(verdict=NOT_CHECKABLE, reason=exc.reason, detail=exc.detail)
    except Unreadable as exc:
        return _unreadable(REASON_SYMBOL_UNREADABLE, exc)
    try:
        pads = electrical_pads(_parse_footprint(footprint))
    except Unreadable as exc:
        return _unreadable(REASON_FOOTPRINT_UNREADABLE, exc)
    return compare(pins, pads)


def _unreadable(reason: str, exc: Exception) -> PinPadResult:
    return PinPadResult(verdict=NOT_CHECKABLE, reason=reason, detail=str(exc)[:200])


# Keyed on the two content hashes: the files are content-addressed, so
# the same pair of hashes is the same pair of files and the same answer.
# Insertion-ordered dict as a bounded FIFO — a miss costs one parse.
_CACHE: dict[tuple[str, str], PinPadResult] = {}
_CACHE_LOCK = threading.Lock()


def check_cached(
    symbol_sha: str, footprint_sha: str, load: Callable[[], tuple[bytes, bytes]]
) -> PinPadResult:
    """`check_bytes` through the hash-keyed cache; `load` runs on a miss.

    `load` may raise (a file gone from disk); nothing is cached then.
    """
    key = (symbol_sha, footprint_sha)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if hit is not None:
        return hit
    result = check_bytes(*load())
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_SIZE:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = result
    return result
