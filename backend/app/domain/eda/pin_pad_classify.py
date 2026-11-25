"""Classify each pin/pad finding by what the pad most likely is (SM-008b).

The first real run of the pin/pad check (276 parts) failed 12, and every
one of them was a pad without a pin: exposed / thermal pads, NC package
pins a symbol leaves out, unnamed `~` and `M` mechanical pads, and
connector shell tabs. Those are worth a human's glance, not a red
verdict, so each finding now carries a `class`, and only `electrical`
findings fail (`pin_pad.py`). This module derives the class from the
footprint's GEOMETRY and pad TYPE first and from names second.

The classes, tried in this order — the first rule that matches wins,
and anything no rule claims is `electrical`. Every rule is written to
miss rather than to excuse: a real open must not read as a warning.

``mechanical``
    The pad's number is `~` (KiCad's "unnamed"; Würth uses it for
    retention pegs) or is a mounting name: `MP`, `MH` with an optional
    number, or exactly `M` (Tag-Connect's TC2030 legs). Pads with no
    copper, `np_thru_hole` pads and pads with an empty number never
    reach a finding at all (they are not electrical, `pin_pad.py`).

``shell``
    The pad is NAMED as a shield / shell: `SH` with an optional number,
    `SHIELD` or `SHELL`. Or the footprint is a CONNECTOR (the part's
    category says so, or the footprint's name / descr / tags carry a
    connector word — `_CONNECTOR_WORDS`) and the pad either sits outside
    the pin row — beyond the matched pads' bounding box, padded by one
    median pad, along the row's narrow axis — or is large (≥ 2× the
    median matched pad) and touches the courtyard edge. Geometry alone
    never makes a shell outside a connector: a DPAK's drain tab is large
    and at the edge, and a symbol missing it is a real open.

``exposed_pad``
    Any of:

    * E1 — the number's largest pad is ≥ 4× the median pad's area, its
      centre lies in the body's central region (the middle half of the
      courtyard in each axis), and it stays clear of the courtyard's
      outer band (15 % of the span): an EP sits inside the body, while
      a DPAK's drain tab — large and fairly central — runs to the edge
      and must stay `electrical`;
    * E2 — the number is numeric and above the symbol's highest numeric
      pin (a QFN-32's `33`), its largest pad is ≥ 2× the median, and it
      is central and clear of the edge as in E1;
    * E3 — every copper pad of the number lies inside an E1-shaped pad
      (whatever that pad's own number): thermal vias numbered on their
      own, `THS4541IRGTR`'s `18`–`22`.

    A signal-sized pad in the middle of an SOIC is NOT exposed: that is
    the SM-008 acceptance case and it stays `electrical`.

``nc_pin``
    A numeric pad number inside the symbol's own numbering — between 1
    and the symbol's highest numeric pin, AND no higher than the count
    of signal-sized numbered pads the footprint has — whose largest pad
    is signal-sized (≤ 2× the median). That is a gap in the symbol's
    numbering: `SOT-23-5` with pins 1, 2, 3, 5 and pad 4.

The "median pad" is the median area of the largest pad of each number
the symbol DOES claim (all numbers when it claims none), so one large
EP or a ring of large shell tabs cannot drag the yardstick. The "body"
is the courtyard's bounding box, else the fab layer's, else the pads'.

A symbol pin without a pad and a duplicate pin number are always
`electrical`: there is no geometry on the symbol side to excuse them.

Pure: parsed footprint in, classes out. No I/O.
"""
from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, replace

from app.domain.eda import sexpr

MECHANICAL = "mechanical"
EXPOSED_PAD = "exposed_pad"
NC_PIN = "nc_pin"
SHELL = "shell"
ELECTRICAL = "electrical"
CLASSES = (EXPOSED_PAD, NC_PIN, SHELL, MECHANICAL, ELECTRICAL)

# Thresholds, as ratios of the median pad (area) or of the body (span).
EP_AREA_FACTOR = 4.0
EP_BEYOND_PIN_AREA_FACTOR = 2.0
SIGNAL_AREA_FACTOR = 2.0
SHELL_AREA_FACTOR = 2.0
CENTRAL_FRACTION = 0.25  # |offset| ≤ 25 % of the span = the middle half
EDGE_FRACTION = 0.15  # within 15 % of the span from the courtyard edge
_EPS = 1e-6

_UNNAMED = "~"
_MECHANICAL_NAME = re.compile(r"(?:MP|MH)\d*|M", re.IGNORECASE)
_SHELL_NAME = re.compile(r"SH\d*|SHIELD|SHELL", re.IGNORECASE)
# Whole words, where `_` and digits also separate (`Molex_MCX_73415`).
_CONNECTOR_WORDS = re.compile(
    r"(?<![A-Za-z])(?:conn|connector|receptacle|jack|socket|header|plug|usb|"
    r"hdmi|rj\d+|sma|smb|mcx|mmcx|u\.?fl|ipex|coax|coaxial|terminal)(?![A-Za-z])",
    re.IGNORECASE,
)
_COPPER_SUFFIX = ".Cu"
_ELECTRICAL_TYPES = frozenset({"smd", "thru_hole", "connect"})
_COURTYARD = frozenset({"F.CrtYd", "B.CrtYd"})
_FAB = frozenset({"F.Fab", "B.Fab"})
_GRAPHICS = frozenset({"fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly"})


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.w * self.h

    def contains(self, other: Box) -> bool:
        return (
            other.x0 >= self.x0 - _EPS
            and other.y0 >= self.y0 - _EPS
            and other.x1 <= self.x1 + _EPS
            and other.y1 <= self.y1 + _EPS
        )

    @staticmethod
    def around(points: list[tuple[float, float]]) -> Box | None:
        if not points:
            return None
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return Box(min(xs), min(ys), max(xs), max(ys))


@dataclass(frozen=True)
class Pad:
    number: str
    box: Box  # axis-aligned bounds of the (possibly rotated) pad


@dataclass(frozen=True)
class Footprint:
    """What classification needs from a `(footprint …)`: its electrical
    pads with geometry, the body box, and the words that may say it is a
    connector."""

    pads: tuple[Pad, ...]
    body: Box | None
    text: str

    @property
    def numbers(self) -> set[str]:
        return {p.number for p in self.pads}


# ---------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------


def _child(node: list, name: str) -> list | None:
    return next(
        (c for c in node if isinstance(c, list) and sexpr.head(c) == name), None
    )


def _floats(node: list | None, count: int) -> tuple[float, ...] | None:
    if node is None or len(node) < count + 1:
        return None
    try:
        return tuple(float(str(v)) for v in node[1 : count + 1])
    except ValueError:
        return None


def _pad_box(pad: list) -> Box | None:
    at = _child(pad, "at")
    size = _floats(_child(pad, "size"), 2)
    centre = _floats(at, 2)
    if centre is None or size is None:
        return None
    angle = 0.0
    if at is not None and len(at) >= 4:
        try:
            angle = math.radians(float(str(at[3])))
        except ValueError:
            angle = 0.0
    w, h = size
    c, s = abs(math.cos(angle)), abs(math.sin(angle))
    half_w, half_h = (w * c + h * s) / 2, (w * s + h * c) / 2
    return Box(centre[0] - half_w, centre[1] - half_h, centre[0] + half_w, centre[1] + half_h)


def _is_electrical(pad: list, number: str) -> bool:
    if not number or len(pad) < 3 or str(pad[2]) not in _ELECTRICAL_TYPES:
        return False
    layers = _child(pad, "layers")
    return layers is not None and any(str(x).endswith(_COPPER_SUFFIX) for x in layers[1:])


def _graphic_points(node: list) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for name in ("start", "mid", "end"):
        xy = _floats(_child(node, name), 2)
        if xy is not None:
            points.append((xy[0], xy[1]))
    centre = _floats(_child(node, "center"), 2)
    if centre is not None and sexpr.head(node) == "fp_circle" and points:
        r = math.dist(centre, points[-1])
        points = [(centre[0] - r, centre[1] - r), (centre[0] + r, centre[1] + r)]
    pts = _child(node, "pts")
    for xy in (c for c in (pts or [])[1:] if isinstance(c, list)):
        pair = _floats(xy, 2)
        if pair is not None:
            points.append((pair[0], pair[1]))
    return points


def _layer_of(node: list) -> str:
    layer = _child(node, "layer")
    return str(layer[1]) if layer is not None and len(layer) > 1 else ""


def footprint_geometry(node: list) -> Footprint:
    """Electrical pads with bounds, the body box and the descriptive text."""
    pads: list[Pad] = []
    courtyard: list[tuple[float, float]] = []
    fab: list[tuple[float, float]] = []
    text = [str(node[1]) if len(node) > 1 else ""]
    for child in node:
        if not isinstance(child, list):
            continue
        head = sexpr.head(child)
        if head == "pad" and len(child) >= 3:
            number = str(child[1]).strip()
            box = _pad_box(child)
            if box is not None and _is_electrical(child, number):
                pads.append(Pad(number, box))
        elif head in _GRAPHICS:
            layer = _layer_of(child)
            if layer in _COURTYARD:
                courtyard += _graphic_points(child)
            elif layer in _FAB:
                fab += _graphic_points(child)
        elif head in ("descr", "tags") and len(child) > 1:
            text.append(str(child[1]))
    body = (
        Box.around(courtyard)
        or Box.around(fab)
        or Box.around([(p.box.x0, p.box.y0) for p in pads] + [(p.box.x1, p.box.y1) for p in pads])
    )
    return Footprint(tuple(pads), body, " ".join(text))


def is_connector_text(text: str | None) -> bool:
    return bool(text) and _CONNECTOR_WORDS.search(text or "") is not None


# ---------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class _Context:
    footprint: Footprint
    main: dict[str, Pad]  # largest pad per number
    median: float
    body: Box | None
    eps: tuple[Box, ...]  # E1-shaped pads, any number
    matched_box: Box | None
    max_pin: int | None
    signal_count: int
    connector: bool


def _main_pads(footprint: Footprint) -> dict[str, Pad]:
    main: dict[str, Pad] = {}
    for pad in footprint.pads:
        if pad.number not in main or pad.box.area > main[pad.number].box.area:
            main[pad.number] = pad
    return main


def _central(ctx: _Context, box: Box) -> bool:
    body = ctx.body
    if body is None or body.w <= 0 or body.h <= 0:
        return False
    return (
        abs(box.cx - body.cx) <= CENTRAL_FRACTION * body.w + _EPS
        and abs(box.cy - body.cy) <= CENTRAL_FRACTION * body.h + _EPS
    )


def _at_edge(ctx: _Context, box: Box) -> bool:
    body = ctx.body
    if body is None:
        return False
    dx, dy = EDGE_FRACTION * body.w, EDGE_FRACTION * body.h
    return (
        box.x0 <= body.x0 + dx
        or box.x1 >= body.x1 - dx
        or box.y0 <= body.y0 + dy
        or box.y1 >= body.y1 - dy
    )


def _inside_body(ctx: _Context, box: Box) -> bool:
    return _central(ctx, box) and not _at_edge(ctx, box)


def _ratio(ctx: _Context, pad: Pad) -> float:
    return pad.box.area / ctx.median if ctx.median > 0 else 0.0


def _context(
    footprint: Footprint, pin_numbers: set[str], connector: bool
) -> _Context:
    main = _main_pads(footprint)
    matched = [p for n, p in main.items() if n in pin_numbers]
    sample = matched or list(main.values())
    median = statistics.median(p.box.area for p in sample) if sample else 0.0
    numeric_pins = [int(n) for n in pin_numbers if n.isdigit()]
    ctx = _Context(
        footprint=footprint,
        main=main,
        median=median,
        body=footprint.body,
        eps=(),
        matched_box=Box.around([(p.box.cx, p.box.cy) for p in matched]),
        max_pin=max(numeric_pins) if numeric_pins else None,
        signal_count=0,
        connector=connector or is_connector_text(footprint.text),
    )
    eps = tuple(
        p.box for p in footprint.pads
        if _ratio(ctx, p) >= EP_AREA_FACTOR and _inside_body(ctx, p.box)
    )
    signal = sum(
        1 for n, p in main.items() if n.isdigit() and _ratio(ctx, p) <= SIGNAL_AREA_FACTOR
    )
    return replace(ctx, eps=eps, signal_count=signal)


def _outside_row(ctx: _Context, box: Box) -> bool:
    """Off the matched pads' row along its narrow axis (both axes when
    the row is square, e.g. one matched pad)."""
    row = ctx.matched_box
    if row is None:
        return False
    pad = math.sqrt(ctx.median) if ctx.median > 0 else 0.0
    off_x = box.cx < row.x0 - pad or box.cx > row.x1 + pad
    off_y = box.cy < row.y0 - pad or box.cy > row.y1 + pad
    if abs(row.w - row.h) <= _EPS:
        return off_x or off_y
    return off_y if row.w > row.h else off_x


def _mechanical(number: str) -> str | None:
    if number == _UNNAMED:
        return "pad is unnamed (`~`)"
    if _MECHANICAL_NAME.fullmatch(number):
        return "pad is named as a mounting / mechanical pad"
    return None


def _shell(ctx: _Context, number: str, pad: Pad) -> str | None:
    if _SHELL_NAME.fullmatch(number):
        return "pad is named as a shield / shell"
    if not ctx.connector:
        return None
    if _outside_row(ctx, pad.box):
        return "connector pad outside the pin row"
    if _ratio(ctx, pad) >= SHELL_AREA_FACTOR and _at_edge(ctx, pad.box):
        return "large connector pad at the courtyard edge"
    return None


def _exposed(ctx: _Context, number: str, pad: Pad) -> str | None:
    if _ratio(ctx, pad) >= EP_AREA_FACTOR and _inside_body(ctx, pad.box):
        return (
            f"pad is {_ratio(ctx, pad):.0f}x the median pad and in the body's centre"
        )
    beyond = number.isdigit() and ctx.max_pin is not None and int(number) > ctx.max_pin
    if beyond and _ratio(ctx, pad) >= EP_BEYOND_PIN_AREA_FACTOR and _inside_body(ctx, pad.box):
        return "pad is numbered beyond the symbol's pins, large, and in the body's centre"
    own = [p.box for p in ctx.footprint.pads if p.number == number]
    if ctx.eps and own and all(
        any(ep.contains(b) and ep != b for ep in ctx.eps) for b in own
    ):
        return "pad lies inside an exposed pad (thermal via)"
    return None


def _nc(ctx: _Context, number: str, pad: Pad) -> str | None:
    if not number.isdigit() or ctx.max_pin is None:
        return None
    value = int(number)
    if not (1 <= value <= ctx.max_pin and value <= ctx.signal_count):
        return None
    if _ratio(ctx, pad) > SIGNAL_AREA_FACTOR:
        return None
    return "signal-sized pad inside the symbol's pin numbering; likely an NC package pin"


def classify_pads(
    footprint: Footprint | None,
    unmatched: set[str],
    pin_numbers: set[str],
    connector: bool = False,
) -> dict[str, tuple[str, str]]:
    """`{pad number: (class, reason)}` for each pad no pin claims.

    Without geometry (`footprint` None) only the name rules apply.
    """
    out: dict[str, tuple[str, str]] = {}
    ctx = _context(footprint, pin_numbers, connector) if footprint is not None else None
    for number in unmatched:
        reason = _mechanical(number)
        if reason is not None:
            out[number] = (MECHANICAL, reason)
            continue
        if _SHELL_NAME.fullmatch(number):
            out[number] = (SHELL, "pad is named as a shield / shell")
            continue
        pad = ctx.main.get(number) if ctx is not None else None
        if ctx is None or pad is None:
            out[number] = (ELECTRICAL, "pad has copper and no symbol pin")
            continue
        out[number] = _classify_geometric(ctx, number, pad)
    return out


def _classify_geometric(ctx: _Context, number: str, pad: Pad) -> tuple[str, str]:
    for cls, rule in ((SHELL, _shell), (EXPOSED_PAD, _exposed), (NC_PIN, _nc)):
        reason = rule(ctx, number, pad)
        if reason is not None:
            return cls, reason
    return ELECTRICAL, "pad has copper and no symbol pin"
