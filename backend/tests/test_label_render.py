"""Label render engine — template + context -> cab JScript.

Pure-function tests: no DB, no HTTP, no printer. Covers the job header, each
element kind, binding resolution, and — the security-relevant part — the
JScript injection guard, which is driven with genuinely hostile input rather
than a token "bad string".
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.printing import label_render
from app.domain.printing.default_templates import BUILT_IN_TEMPLATES


class _Template:
    """The duck-typed shape `render` reads — geometry attrs + `elements`."""

    def __init__(self, elements: list[dict[str, Any]], **geometry: Any) -> None:
        self.width_mm = geometry.get("width_mm", 50.0)
        self.height_mm = geometry.get("height_mm", 30.0)
        self.gap_mm = geometry.get("gap_mm", 3.0)
        self.heat = geometry.get("heat", 100)
        self.speed = geometry.get("speed", 0)
        self.method = geometry.get("method", "T")
        self.elements = elements


def _ctx(**overrides: str) -> dict[str, str]:
    base = {"code": "AB3Q7K2M", "url": "https://example.test/c/AB3Q7K2M", "name": "Widget"}
    base.update(overrides)
    return base


def _lines(jscript: str) -> list[str]:
    return [line for line in jscript.split("\r\n") if line]


# ---------------------------------------------------------------------------
# Job header / footer
# ---------------------------------------------------------------------------


def test_render_emits_job_header_and_print_count():
    out = label_render.render(_Template([]), _ctx())
    lines = _lines(out)

    assert lines[0] == "m m"          # units = mm
    assert lines[1] == "J"            # start of job
    assert lines[2] == "H 100,0,T"    # heat, speed, thermal-transfer
    # S <ptype>;<xo>,<yo>,<height>,<pitch>,<width> — pitch = height + gap
    assert lines[3] == "S l1;0,0,30,33,50"
    assert lines[-1] == "A 1"         # one copy
    assert out.endswith("\r\n")


def test_render_copies_land_in_the_print_count_line():
    out = label_render.render(_Template([]), _ctx(), copies=7)
    assert _lines(out)[-1] == "A 7"


def test_render_rejects_zero_copies():
    with pytest.raises(label_render.LabelRenderError):
        label_render.render(_Template([]), _ctx(), copies=0)


def test_render_rejects_non_list_elements():
    template = _Template([])
    template.elements = {"kind": "text"}  # type: ignore[assignment]
    with pytest.raises(label_render.LabelRenderError):
        label_render.render(template, _ctx())


def test_geometry_columns_drive_the_s_command():
    out = label_render.render(
        _Template([], width_mm=100, height_mm=20, gap_mm=2, heat=80, speed=50, method="D"),
        _ctx(),
    )
    lines = _lines(out)
    assert lines[2] == "H 80,50,D"
    assert lines[3] == "S l1;0,0,20,22,100"


# ---------------------------------------------------------------------------
# Element kinds
# ---------------------------------------------------------------------------


def test_qr_defaults_to_the_url_binding():
    """A QR with no text and no binding is the scan target — that is the
    whole point of putting one on a label."""
    out = label_render.render(
        _Template([{"kind": "qr", "x_mm": 2, "y_mm": 2}]), _ctx()
    )
    qr = [line for line in _lines(out) if line.startswith("B ")][0]
    assert qr.endswith(";https://example.test/c/AB3Q7K2M")
    assert "QRCODE+ELM+MODEL2" in qr


def test_qr_honours_an_explicit_binding_and_ec_level():
    out = label_render.render(
        _Template([{"kind": "qr", "binding": "code", "ec": "H", "dotsize_mm": 0.8}]),
        _ctx(),
    )
    qr = [line for line in _lines(out) if line.startswith("B ")][0]
    assert "+ELH+MODEL2" in qr
    assert ",0.8;AB3Q7K2M" in qr


def test_unknown_ec_level_falls_back_to_m():
    out = label_render.render(_Template([{"kind": "qr", "ec": "Z"}]), _ctx())
    assert "+ELM+MODEL2" in out


def test_text_element_renders_a_t_command_with_resolved_binding():
    out = label_render.render(
        _Template([{"kind": "text", "x_mm": 5, "y_mm": 0, "binding": "name",
                    "font": 5, "size_pt": 9}]),
        _ctx(),
    )
    text_line = [line for line in _lines(out) if line.startswith("T ")][0]
    assert text_line.endswith(";Widget")
    assert ",5,pt9" in text_line  # font 5, 9pt


def test_text_literal_may_itself_contain_a_binding():
    out = label_render.render(
        _Template([{"kind": "text", "text": "PN {{code}} / {{name}}"}]), _ctx()
    )
    assert ";PN AB3Q7K2M / Widget" in out


def test_text_that_resolves_to_nothing_emits_no_command():
    """An empty T line is still a command the printer processes; skip it."""
    out = label_render.render(
        _Template([{"kind": "text", "binding": "nonexistent"}]), _ctx()
    )
    assert not [line for line in _lines(out) if line.startswith("T ")]


def test_unknown_binding_resolves_to_empty_not_an_error():
    out = label_render.render(
        _Template([{"kind": "text", "text": "x{{nope}}y"}]), _ctx()
    )
    assert ";xy" in out


def test_barcode1d_renders_a_b_command():
    out = label_render.render(
        _Template([{"kind": "barcode1d", "binding": "code", "bc_type": "CODE39",
                    "height_mm": 6, "ne_mm": 0.3}]),
        _ctx(),
    )
    bc = [line for line in _lines(out) if line.startswith("B ")][0]
    assert "CODE39,6,0.3;AB3Q7K2M" in bc


def test_handwriting_renders_the_g_line_element():
    """`G x,y,r;L:length,width` — the semicolon before `L:` is load-bearing;
    the comma form is rejected by the printer with status error B."""
    out = label_render.render(
        _Template([{"kind": "handwriting", "x_mm": 4, "y_mm": 20, "w_mm": 30,
                    "h_mm": 0.4}]),
        _ctx(),
    )
    assert "G 4,20,0;L:30,0.4" in out


def test_unknown_element_kind_is_skipped_not_fatal():
    """A template outliving a kind rename prints the rest of the label."""
    out = label_render.render(
        _Template([{"kind": "hologram"}, {"kind": "text", "binding": "name"}]),
        _ctx(),
    )
    assert ";Widget" in out


def test_non_dict_elements_are_ignored():
    out = label_render.render(_Template(["nope", None, 42]), _ctx())  # type: ignore[list-item]
    assert _lines(out)[-1] == "A 1"


# ---------------------------------------------------------------------------
# Rotation + text anchoring
# ---------------------------------------------------------------------------


def test_editor_rotation_is_negated_for_cab():
    """The designer rotates clockwise; cab rotates counter-clockwise."""
    out = label_render.render(
        _Template([{"kind": "qr", "x_mm": 0, "y_mm": 0, "rotation": 90}]), _ctx()
    )
    assert _lines(out)[4].startswith("B 0,0,270,")


def test_text_anchor_shifts_down_by_the_font_ascent():
    """Every other element anchors top-left; cab anchors text at the
    baseline, so an unshifted y prints one ascent too high."""
    out = label_render.render(
        _Template([{"kind": "text", "x_mm": 10, "y_mm": 0, "size_pt": 10,
                    "text": "x"}]),
        _ctx(),
    )
    line = [ln for ln in _lines(out) if ln.startswith("T ")][0]
    x, y = line[2:].split(",")[:2]
    assert float(x) == 10
    assert 2.5 < float(y) < 3.0  # 0.8 * 10pt * 0.352778 mm/pt ~= 2.82


# ---------------------------------------------------------------------------
# Named TrueType fonts
# ---------------------------------------------------------------------------


def test_named_font_is_declared_once_and_referenced_by_number():
    """A downloaded font is addressed by NUMBER in `T`; without the `F`
    declaration the printer rejects the job with status error B."""
    out = label_render.render(
        _Template([
            {"kind": "text", "font": "mybold", "text": "a"},
            {"kind": "text", "font": "mybold", "text": "b"},
        ]),
        _ctx(),
    )
    lines = _lines(out)
    assert lines.count("F 10;mybold") == 1
    assert all(",10,pt" in ln for ln in lines if ln.startswith("T "))


def test_two_named_fonts_get_distinct_numbers():
    out = label_render.render(
        _Template([
            {"kind": "text", "font": "alpha", "text": "a"},
            {"kind": "text", "font": "beta", "text": "b"},
        ]),
        _ctx(),
    )
    assert "F 10;alpha" in out
    assert "F 11;beta" in out


# ---------------------------------------------------------------------------
# JScript injection guard — the security-relevant surface
# ---------------------------------------------------------------------------

# Each case is a real attack shape, not a generic "bad string":
#   1. CRLF + `A 500` — end the T command, start a new one, print 500 labels.
#   2. bare LF + `J` — some parsers accept LF alone as a separator.
#   3. `;` — terminate the T command's parameter list and forge a new field.
#   4. CR + `S l1;...` — redefine the label geometry mid-job.
#   5. NUL and other C0 bytes — control codes the interpreter may act on.
_HOSTILE = [
    "Widget\r\nA 500",
    "Widget\nJ\nH 100,0,T",
    "Widget;T 0,0,0,3,pt10;pwned",
    "Widget\rS l1;0,0,999,999,999",
    "Widget\x00\x07\x1b[2J",
]


@pytest.mark.parametrize("hostile", _HOSTILE)
def test_sanitize_strips_every_jscript_separator(hostile: str):
    cleaned = label_render.sanitize(hostile)
    assert "\r" not in cleaned
    assert "\n" not in cleaned
    assert ";" not in cleaned
    assert "\x00" not in cleaned
    assert "\x1b" not in cleaned
    assert cleaned.startswith("Widget")


@pytest.mark.parametrize("hostile", _HOSTILE)
def test_hostile_text_binding_cannot_inject_a_jscript_command(hostile: str):
    """The attack: a part name / lot serial / supplier the operator does not
    control, arriving through a binding and closing the T command."""
    out = label_render.render(
        _Template([{"kind": "text", "binding": "name"}]), _ctx(name=hostile)
    )
    lines = _lines(out)

    # Exactly one T command, and no forged commands anywhere.
    assert len([ln for ln in lines if ln.startswith("T ")]) == 1
    assert not [ln for ln in lines if ln.startswith("A ") and ln != "A 1"]
    assert len([ln for ln in lines if ln.startswith("S ")]) == 1
    assert len([ln for ln in lines if ln == "J"]) == 1
    assert len([ln for ln in lines if ln.startswith("H ")]) == 1
    # The line count is exactly header(4) + our T + footer(1).
    assert len(lines) == 6


@pytest.mark.parametrize("hostile", _HOSTILE)
def test_hostile_qr_payload_cannot_inject_a_jscript_command(hostile: str):
    """`;` terminates the B command's data just as it does T's."""
    out = label_render.render(
        _Template([{"kind": "qr", "binding": "url"}]), _ctx(url=hostile)
    )
    lines = _lines(out)
    assert len([ln for ln in lines if ln.startswith("B ")]) == 1
    assert len(lines) == 6


@pytest.mark.parametrize("hostile", _HOSTILE)
def test_hostile_literal_in_the_template_is_sanitised_too(hostile: str):
    """A template body is operator-supplied, but an admin account is not a
    licence to drive the printer out-of-band."""
    out = label_render.render(_Template([{"kind": "text", "text": hostile}]), _ctx())
    assert len(_lines(out)) == 6


def test_hostile_barcode_payload_cannot_inject_a_jscript_command():
    out = label_render.render(
        _Template([{"kind": "barcode1d", "binding": "code"}]),
        _ctx(code="X;B 0,0,0,CODE128,10,0.4;evil"),
    )
    assert len(_lines(out)) == 6


def test_injection_via_a_named_font_is_sanitised():
    """The font NAME also lands in a JScript line (`F <n>;<NAME>`)."""
    out = label_render.render(
        _Template([{"kind": "text", "font": "ok\r\nA 99", "text": "a"}]), _ctx()
    )
    lines = _lines(out)
    assert not [ln for ln in lines if ln.startswith("A ") and ln != "A 1"]
    # header(4) + F declaration + T + footer(1)
    assert len(lines) == 7
    font_line = [ln for ln in lines if ln.startswith("F ")][0]
    assert font_line.startswith("F 10;ok")
    assert "\r" not in font_line and "\n" not in font_line


def test_sanitised_text_is_length_capped():
    out = label_render.render(
        _Template([{"kind": "text", "binding": "name"}]), _ctx(name="x" * 5000)
    )
    text_line = [ln for ln in _lines(out) if ln.startswith("T ")][0]
    assert len(text_line.split(";", 1)[1]) == 2000


# ---------------------------------------------------------------------------
# The built-in catalog renders
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entity_type", sorted(BUILT_IN_TEMPLATES))
def test_every_built_in_default_renders_to_valid_jscript(entity_type: str):
    spec = BUILT_IN_TEMPLATES[entity_type]
    geometry = {k: v for k, v in spec.items() if k not in ("elements", "name", "dpi")}
    out = label_render.render(_Template(list(spec["elements"]), **geometry), _ctx())
    lines = _lines(out)
    assert lines[0] == "m m"
    assert lines[-1] == "A 1"
    # Every default carries the scan QR and the human-readable code.
    assert any(ln.startswith("B ") and "QRCODE" in ln for ln in lines)
    assert any(ln.endswith(";AB3Q7K2M") for ln in lines)
