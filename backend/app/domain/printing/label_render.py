"""Template + context -> cab JScript.

Ported from the sibling skladVA project (/mnt/data/WORK/sklad,
``backend/app/printing/label_render.py``), trimmed to this project's needs: the
namespace-URL / short-link helpers are gone (this codebase has exactly one code
namespace — ``/c/{code}``, from #892 — and the URL is assembled by
:mod:`app.domain.printing.template_service`, which is the layer that knows both
the settings and the DB).

What this module does: take a :class:`~app.domain.printing.models.LabelTemplate`
(its geometry columns + ``elements`` JSONB list) plus a *context* dict, and emit
a complete JScript program by composing the ALREADY-VENDORED ``Job`` / ``Text``
/ ``Barcode1D`` / ``Barcode2D`` classes from
:mod:`app.domain.printing.cab_squix` (#890). It does not re-implement any of
the wire syntax.

**Pure string rendering — no I/O, no DB, no settings.** Everything variable
arrives in ``context``. The transport lives in
:mod:`app.domain.printing.print_service` (``send_jscript``); the context is
assembled by :mod:`app.domain.printing.template_service`.

Binding resolution
------------------
An element's text / barcode payload may carry ``{{token}}`` bindings resolved
from the context. A ``qr`` element with no explicit text or binding defaults to
``{{url}}`` — the scan-to-open URL for the object's code — because that is what
a QR on a label is *for*. An unknown token resolves to the empty string rather
than raising: a template outliving a binding rename should print a slightly
emptier label, not fail the whole job.

JScript injection guard
-----------------------
JScript is a line-oriented protocol: commands are separated by CR/LF and a
command's parameters from its data by ``;``. A part named
``"widget\\r\\nA 500"`` interpolated raw into a ``T`` command would therefore
end the text command and start a *new* one telling the printer to run off 500
labels. So every resolved free-text fragment goes through :func:`sanitize`,
which replaces all C0 control characters (CR and LF among them) with spaces and
strips ``;``. It runs on the resolved value — after binding substitution — so
it covers both a hostile literal in the template and a hostile value arriving
through a binding (a part name, an order supplier, a scanned lot serial: all
user-controlled). QR payloads are sanitised on the same path, because ``;``
terminates the ``B`` command's data just as it does ``T``'s.

Handwriting fields
------------------
A ``handwriting`` element renders as a thin rule for the operator to write on,
using the JScript ``G`` Line element — ``G x,y,r;L:length,width`` (cab JScript
manual §4.7.2) draws a ``width`` mm thick line ``length`` mm long.
"""

from __future__ import annotations

import math
import re
from typing import Any

from app.domain.printing.cab_squix import Barcode1D, Barcode2D, Job, Text

# ``{{token}}``, tolerating whitespace inside the braces.
_BINDING_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# C0 control characters. CR and LF are the JScript command separators; the rest
# are stripped for the same defence-in-depth reason (a printer that reacts to
# any of them is a printer we do not want to hand them to).
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")

# The JScript field terminator. Everything after it on a ``T``/``B`` line is
# data, so an unescaped one inside data lets the caller forge a new field.
_FIELD_TERMINATOR = ";"

# cab device font ids (JScript manual): 3 = Swiss 721, 5 = Swiss 721 Bold,
# 596 = Monospace 821.
_DEFAULT_TEXT_FONT = 3
_DEFAULT_TEXT_SIZE_PT = 8

# QR defaults when the element omits them.
_DEFAULT_QR_DOTSIZE_MM = 0.5
_DEFAULT_QR_EC = "M"  # error correction L/M/Q/H -> +ELL/+ELM/+ELQ/+ELH
_QR_EC_LEVELS = ("L", "M", "Q", "H")

# Barcode1D defaults.
_DEFAULT_BC_TYPE = "CODE128"
_DEFAULT_BC_HEIGHT_MM = 8.0
_DEFAULT_BC_NE_MM = 0.4

# Handwriting rule length/thickness (mm) when not given.
_DEFAULT_HANDWRITING_WIDTH_MM = 20.0
_DEFAULT_HANDWRITING_THICKNESS_MM = 0.3

# Cap on one rendered free-text field. The cab ``T`` field accepts ~2725
# characters; staying well under that still allows a long description line
# while bounding what a single binding can push into the job.
_TEXT_MAX_LEN = 2000

# Job-header fallbacks, used only when a template column is somehow unreadable
# (the DB columns are NOT NULL, so in practice these never fire).
_FALLBACK_WIDTH_MM = 50.0
_FALLBACK_HEIGHT_MM = 30.0
_FALLBACK_GAP_MM = 3.0
_FALLBACK_HEAT = 100
_FALLBACK_SPEED = 0

# Text vertical anchoring. Every other element (QR, barcode, rule) treats
# ``y_mm`` as its TOP edge, but the cab ``T`` command anchors text at its font
# BASELINE — the glyphs sit ABOVE y (JScript manual p.225). Without correction
# text prints one ascent too high relative to everything else on the label. We
# convert the stored top-left y to the baseline y cab wants by shifting down by
# the font ascent (~0.8 em), rotated so the shift follows the glyph's up-axis.
_PT_TO_MM = 0.352778
_TEXT_ASCENT_FACTOR = 0.8

# Number assigned to the first downloaded TrueType font. A downloaded font is
# addressed by NUMBER in the ``T`` command, not by name, so each named font is
# declared once with ``F <n>;<NAME>`` and referenced by its number thereafter.
_FONT_NUMBER_BASE = 10


class LabelRenderError(ValueError):
    """Raised when a template/context cannot be rendered to JScript."""


def sanitize(value: str) -> str:
    """Strip JScript-significant characters from a free-text fragment.

    Replaces every C0 control character (CR and LF, the command separators,
    among them) with a space and removes ``;`` (the field terminator). This is
    the injection guard: it is what stops a part name, a lot serial or an order
    supplier from closing the current JScript command and opening another.

    Public because it is the security-relevant half of this module and is
    tested directly against hostile input.
    """
    cleaned = _CONTROL_CHARS_RE.sub(" ", value).replace(_FIELD_TERMINATOR, " ")
    return cleaned.strip()


def resolve_bindings(template_text: str, context: dict[str, str]) -> str:
    """Substitute ``{{token}}`` occurrences from ``context``.

    An unknown token resolves to "" — see the module docstring on why this is
    lenient rather than an error.
    """

    def _sub(match: re.Match[str]) -> str:
        return str(context.get(match.group(1), ""))

    return _BINDING_RE.sub(_sub, template_text)


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_text(element: dict[str, Any], context: dict[str, str]) -> str:
    """Resolve an element's display text from a literal or a binding.

    A literal ``text`` wins; otherwise the ``binding`` token is looked up. The
    result is binding-substituted (so a literal may itself embed ``{{code}}``),
    then sanitised and length-capped. Sanitising AFTER substitution is the
    point — it is the substituted value that is user-controlled.
    """
    raw = element.get("text")
    if raw is None:
        binding = element.get("binding")
        raw = f"{{{{{binding}}}}}" if binding else ""
    return sanitize(resolve_bindings(str(raw), context))[:_TEXT_MAX_LEN]


def _qr_payload(element: dict[str, Any], context: dict[str, str]) -> str:
    """Resolve a QR element's payload, defaulting to the ``{{url}}`` binding."""
    if element.get("text") is not None:
        raw = str(element["text"])
    else:
        raw = "{{" + str(element.get("binding") or "url") + "}}"
    # QR data is not "free text" in the visual sense, but ``;`` still
    # terminates the B-command's data, so it goes through the same guard.
    return sanitize(resolve_bindings(raw, context))[:_TEXT_MAX_LEN]


def _ec_options(ec: Any) -> str:
    """Map an error-correction level (L/M/Q/H) to the QR ``+EL?`` option."""
    level = str(ec or _DEFAULT_QR_EC).upper()
    if level not in _QR_EC_LEVELS:
        level = _DEFAULT_QR_EC
    return f"+EL{level}+MODEL2"


def _is_named_font(value: Any) -> bool:
    """True for a downloaded-TrueType font name (as opposed to a device id)."""
    return isinstance(value, str) and bool(value.strip()) and not value.strip().isdigit()


def _font(value: Any, font_numbers: dict[str, int]) -> int:
    """Resolve a text element's font to the number emitted in ``T``."""
    if _is_named_font(value):
        return font_numbers.get(value.strip(), _FONT_NUMBER_BASE)
    return _int(value, _DEFAULT_TEXT_FONT)


def _cab_rotation(value: Any) -> int:
    """Convert an editor (clockwise) rotation to cab's counter-clockwise one.

    The designer rotates clockwise, as CSS ``rotate()`` does; the cab ``T``/
    ``B`` rotation is counter-clockwise about the same top-left anchor. 90° CW
    is 270° CCW.
    """
    return (360 - _int(value, 0)) % 360


def _text_baseline_anchor(
    x: float, y: float, size_pt: int, rotation_cw: int
) -> tuple[float, float]:
    """Shift a top-left text anchor to the baseline anchor cab expects.

    The shift is the local down-vector ``(0, ascent)`` put through the same
    clockwise rotation the designer applies (origin: top-left), which in the
    label's y-down frame maps to ``(-ascent*sin, +ascent*cos)``. At 0° it drops
    straight down; at 90° it moves in -x, keeping a rotated caption on the same
    side of its neighbour as the preview shows.
    """
    ascent_mm = _TEXT_ASCENT_FACTOR * size_pt * _PT_TO_MM
    phi = math.radians(rotation_cw)
    return (x - ascent_mm * math.sin(phi), y + ascent_mm * math.cos(phi))


class _RawLine:
    """An Element-like wrapper emitting a pre-built raw JScript line.

    ``Job`` calls ``.to_jscript()`` on every registered element, so this is how
    the ``G`` (handwriting rule) and ``F`` (font declaration) lines — which the
    vendored driver has no dataclass for — reach the output through the same
    path as everything else, rather than by string-splicing the finished job.
    """

    __slots__ = ("_line",)

    def __init__(self, line: str) -> None:
        self._line = line

    def to_jscript(self) -> str:
        return self._line


def _handwriting_line(element: dict[str, Any]) -> str:
    """A handwriting field: a thin rule via the JScript ``G`` Line element.

    Per the cab manual §4.7.2 the form is ``G[:name;]x,y,r;L:length,width`` —
    note the element spec is introduced by a SEMICOLON, not a comma. (The
    ``G x,y,r,box:...`` form is invalid and the printer rejects the whole job
    with status error ``B``; skladVA learned that the hard way.)
    """
    x = _float(element.get("x_mm"), 0.0)
    y = _float(element.get("y_mm"), 0.0)
    r = _cab_rotation(element.get("rotation"))
    length = _float(element.get("w_mm"), _DEFAULT_HANDWRITING_WIDTH_MM)
    thickness = _float(element.get("h_mm"), _DEFAULT_HANDWRITING_THICKNESS_MM)
    return f"G {x:g},{y:g},{r};L:{length:g},{thickness:g}"


def _add_element(
    job: Job,
    element: dict[str, Any],
    context: dict[str, str],
    font_numbers: dict[str, int],
) -> None:
    """Append one template element to ``job``. Unknown kinds are skipped."""
    kind = element.get("kind")
    x = _float(element.get("x_mm"), 0.0)
    y = _float(element.get("y_mm"), 0.0)
    rotation = _cab_rotation(element.get("rotation"))

    if kind == "qr":
        job.add(
            Barcode2D(
                x,
                y,
                _qr_payload(element, context),
                type="QRCODE",
                dotsize_mm=_float(element.get("dotsize_mm"), _DEFAULT_QR_DOTSIZE_MM),
                rotation=rotation,
                options=_ec_options(element.get("ec")),
            )
        )
        return

    if kind == "text":
        text_value = _resolve_text(element, context)
        if not text_value:
            # A binding that resolved to nothing: emit no command at all
            # rather than an empty ``T`` line the printer would still process.
            return
        size_pt = _int(element.get("size_pt"), _DEFAULT_TEXT_SIZE_PT)
        tx, ty = _text_baseline_anchor(
            x, y, size_pt, _int(element.get("rotation"), 0)
        )
        job.add(
            Text(
                tx,
                ty,
                text_value,
                font=_font(element.get("font"), font_numbers),
                size_pt=size_pt,
                rotation=rotation,
            )
        )
        return

    if kind == "barcode1d":
        payload = _resolve_text(element, context)
        if not payload:
            return
        job.add(
            Barcode1D(
                x,
                y,
                payload,
                type=str(element.get("bc_type") or _DEFAULT_BC_TYPE),
                height_mm=_float(element.get("height_mm"), _DEFAULT_BC_HEIGHT_MM),
                ne_mm=_float(element.get("ne_mm"), _DEFAULT_BC_NE_MM),
                rotation=rotation,
            )
        )
        return

    if kind == "handwriting":
        job.elements.append(_RawLine(_handwriting_line(element)))
        return

    # Anything else is skipped in silence. The API validates ``kind`` against
    # ELEMENT_KINDS on write, so reaching here means a template predates a kind
    # rename — printing the rest of the label beats failing the job.


def _declare_fonts(job: Job, elements: list[Any]) -> dict[str, int]:
    """Assign a number to each named TrueType font and declare it.

    A downloaded font is addressed by NUMBER in the ``T`` command, so each name
    gets one ``F <n>;<NAME>`` declaration up front. Without it the printer
    rejects the job with status error "B". Returns the name -> number map.
    """
    font_numbers: dict[str, int] = {}
    for element in elements:
        if not isinstance(element, dict) or element.get("kind") != "text":
            continue
        font = element.get("font")
        if _is_named_font(font):
            name = font.strip()
            if name not in font_numbers:
                font_numbers[name] = _FONT_NUMBER_BASE + len(font_numbers)
    for name, number in font_numbers.items():
        # The font NAME lands in a JScript line, so it goes through the guard
        # like any other interpolated string.
        job.elements.append(_RawLine(f"F {number};{sanitize(name)}"))
    return font_numbers


def render(template: Any, context: dict[str, str], *, copies: int = 1) -> str:
    """Render ``template`` against ``context`` to a complete JScript program.

    ``template`` is a :class:`~app.domain.printing.models.LabelTemplate`, or
    any object exposing the same geometry attributes plus an ``elements`` list
    of dicts. ``context`` maps binding tokens to already-resolved values (see
    ``template_service.build_context``). Returns the JScript text; raises
    :class:`LabelRenderError` if the template cannot be rendered.
    """
    if copies < 1:
        raise LabelRenderError("copies must be >= 1")

    elements = getattr(template, "elements", None) or []
    if not isinstance(elements, list):
        raise LabelRenderError("template.elements must be a list")

    job = Job(
        width_mm=_float(getattr(template, "width_mm", None), _FALLBACK_WIDTH_MM),
        height_mm=_float(getattr(template, "height_mm", None), _FALLBACK_HEIGHT_MM),
        gap_mm=_float(getattr(template, "gap_mm", None), _FALLBACK_GAP_MM),
        heat=_int(getattr(template, "heat", None), _FALLBACK_HEAT),
        speed=_int(getattr(template, "speed", None), _FALLBACK_SPEED),
        method=str(getattr(template, "method", None) or "T"),
        copies=copies,
    )

    font_numbers = _declare_fonts(job, elements)
    for element in elements:
        if isinstance(element, dict):
            _add_element(job, element, context, font_numbers)

    return job.to_jscript()


__all__ = ["LabelRenderError", "render", "sanitize", "resolve_bindings"]
