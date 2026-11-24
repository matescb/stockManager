"""SM-008 pin/pad rules, on library fixtures (no database).

Each fixture under `fixtures/eda/pin_pad/` is a KiCad 8 file built to
exercise one rule; the MCP-level behaviour (lookup, isolation, paging)
is in `test_mcp_pin_pad.py`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.eda import pin_pad

FIXTURES = Path(__file__).parent / "fixtures" / "eda" / "pin_pad"


def _check(symbol: str, footprint: str) -> pin_pad.PinPadResult:
    return pin_pad.check_bytes(
        (FIXTURES / symbol).read_bytes(), (FIXTURES / footprint).read_bytes()
    )


def test_pins_1_to_8_and_an_unnumbered_mechanical_pad_pass():
    """Acceptance: pins 1-8, pads 1-8 + unnumbered mechanical + NPTH."""
    result = _check("soic8.kicad_sym", "soic8_mech.kicad_mod")
    assert result.verdict == pin_pad.PASS
    assert result.findings() == {
        "pins_without_pad": [],
        "pads_without_pin": [],
        "duplicate_pin_numbers": [],
        "no_connect_pins_without_pad": [],
    }


def test_an_electrical_pad_9_with_no_pin_9_fails_naming_pad_9():
    """Acceptance: pads 1-9, pad 9 electrical, no pin 9."""
    result = _check("soic8.kicad_sym", "soic8_pad9.kicad_mod")
    assert result.verdict == pin_pad.FAIL
    assert result.pads_without_pin == ("9",)
    assert result.pins_without_pad == ()


def test_exposed_pad_with_thermal_vias_and_paste_apertures_passes():
    """EP numbered like pin 9, four vias also "9", paste-only "" and "9"."""
    result = _check("qfn8_ep.kicad_sym", "qfn8_ep_vias.kicad_mod")
    assert result.verdict == pin_pad.PASS
    assert result.counts == {"pins": 9, "pads": 9}


def test_paste_only_pad_does_not_count_as_a_pad():
    """A pad on F.Paste alone is no copper: pin 9 is then unmatched."""
    footprint = (FIXTURES / "qfn8_ep_vias.kicad_mod").read_text()
    copper_9 = [
        line for line in footprint.splitlines()
        if '(pad "9"' in line and '"F.Paste")' not in line
    ]
    stripped = "\n".join(line for line in footprint.splitlines() if line not in copper_9)
    result = pin_pad.check_bytes(
        (FIXTURES / "qfn8_ep.kicad_sym").read_bytes(), stripped.encode()
    )
    assert result.verdict == pin_pad.FAIL
    assert result.pins_without_pad == ("9",)


def test_multi_unit_symbol_is_the_union_of_its_units():
    result = _check("dual_opamp.kicad_sym", "soic8_mech.kicad_mod")
    assert result.verdict == pin_pad.PASS
    assert result.counts["pins"] == 8


def test_no_connect_pin_without_a_pad_is_information_not_failure():
    result = _check("nc_pin.kicad_sym", "soic8_no_pad5.kicad_mod")
    assert result.verdict == pin_pad.PASS
    assert result.no_connect_pins_without_pad == ("5",)
    assert result.pins_without_pad == ()


def test_a_non_nc_pin_without_a_pad_fails():
    result = _check("soic8.kicad_sym", "soic8_no_pad5.kicad_mod")
    assert result.verdict == pin_pad.FAIL
    assert result.pins_without_pad == ("5",)


def test_duplicate_pin_number_in_one_unit_fails_but_stacked_and_hidden_power_do_not():
    result = _check("dup_pins.kicad_sym", "soic8_mech.kicad_mod")
    assert result.verdict == pin_pad.FAIL
    # "3" twice at two positions; "4" stacked and "8" hidden power are allowed.
    assert result.duplicate_pin_numbers == ("3",)
    assert result.pins_without_pad == () and result.pads_without_pin == ()


def test_derived_symbol_is_not_checkable():
    result = _check("derived.kicad_sym", "soic8_mech.kicad_mod")
    assert result.verdict == pin_pad.NOT_CHECKABLE
    assert result.reason == pin_pad.REASON_SYMBOL_DERIVED


def test_body_styles_that_number_pins_differently_are_not_checkable():
    result = _check("demorgan_mismatch.kicad_sym", "soic8_mech.kicad_mod")
    assert result.verdict == pin_pad.NOT_CHECKABLE
    assert result.reason == pin_pad.REASON_BODY_STYLES_DIFFER


def test_bga_numbers_are_trimmed_and_case_sensitive():
    result = _check("bga4.kicad_sym", "bga4.kicad_mod")
    assert result.verdict == pin_pad.FAIL
    # " B1 " trims to B1 and matches; "b2" is not "B2".
    assert result.pins_without_pad == ("B2",)
    assert result.pads_without_pin == ("b2",)


@pytest.mark.parametrize(
    ("symbol", "footprint", "reason"),
    [
        (b"(symbol", b"(footprint \"X\")", pin_pad.REASON_SYMBOL_UNREADABLE),
        (None, b"(footprint \"X\"", pin_pad.REASON_FOOTPRINT_UNREADABLE),
        (None, b"(symbol \"X\")", pin_pad.REASON_FOOTPRINT_UNREADABLE),
        (b"\xff\xfe", b"(footprint \"X\")", pin_pad.REASON_SYMBOL_UNREADABLE),
    ],
)
def test_unparseable_files_are_not_checkable(symbol, footprint, reason):
    symbol = symbol if symbol is not None else (FIXTURES / "soic8.kicad_sym").read_bytes()
    result = pin_pad.check_bytes(symbol, footprint)
    assert result.verdict == pin_pad.NOT_CHECKABLE
    assert result.reason == reason


def test_kicad6_bare_hide_atom_marks_a_pin_hidden():
    symbol = (
        b'(symbol "P" (symbol "P_1_1"'
        b' (pin power_in line (at 0 0 0) (length 1) (name "V") (number "1"))'
        b' (pin power_in line (at 0 5 0) (length 1) hide (name "V") (number "1"))))'
    )
    footprint = b'(footprint "F" (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu")))'
    assert pin_pad.check_bytes(symbol, footprint).verdict == pin_pad.PASS


def test_unnumbered_pins_are_ignored_and_legacy_module_root_parses():
    symbol = b'(symbol "P" (symbol "P_1_1" (pin passive line (at 0 0 0) (number ""))))'
    footprint = b'(module "F" (pad "" smd rect (at 0 0) (size 1 1) (layers "F.Cu")))'
    assert pin_pad.check_bytes(symbol, footprint).verdict == pin_pad.PASS


def test_natural_ordering_of_findings():
    pins = [
        pin_pad.Pin(n, "passive", 1, 1, ("0", n), False) for n in ("10", "2", "A10", "A2")
    ]
    result = pin_pad.compare(pins, set())
    assert result.pins_without_pad == ("2", "10", "A2", "A10")


def test_cache_hit_skips_the_loader():
    calls = []

    def load():
        calls.append(1)
        return (
            (FIXTURES / "soic8.kicad_sym").read_bytes(),
            (FIXTURES / "soic8_mech.kicad_mod").read_bytes(),
        )

    first = pin_pad.check_cached("sym-cache-test", "fp-cache-test", load)
    second = pin_pad.check_cached("sym-cache-test", "fp-cache-test", load)
    assert first is second and len(calls) == 1
