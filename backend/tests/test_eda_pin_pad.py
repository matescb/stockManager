"""SM-008 pin/pad rules, on library fixtures (no database).

Each fixture under `fixtures/eda/pin_pad/` is a KiCad 8 file built to
exercise one rule; the MCP-level behaviour (lookup, isolation, paging)
is in `test_mcp_pin_pad.py`.
"""
from __future__ import annotations

import re
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
        "classified": [],
        "pins_without_pad": [],
        "pads_without_pin": [],
        "duplicate_pin_numbers": [],
        "no_connect_pins_without_pad": [],
        "mounting_pads_without_pin": [],
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


def test_connector_mp_retention_pads_are_a_mechanical_warning_not_failure():
    result = _check("jst_sh4.kicad_sym", "jst_sh4_mp.kicad_mod")
    assert result.verdict == pin_pad.PASS_WITH_WARNINGS
    assert result.mounting_pads_without_pin == ("MP",)
    assert result.pads_without_pin == ()
    assert [(f.kind, f.number, f.cls) for f in result.classified] == [
        ("pad_without_pin", "MP", "mechanical")
    ]


def test_usb_c_shield_legs_are_a_shell_warning_not_failure():
    result = _check("usb_c.kicad_sym", "usb_c_shield.kicad_mod")
    assert result.verdict == pin_pad.PASS_WITH_WARNINGS
    assert result.mounting_pads_without_pin == ("SH",)
    assert [f.cls for f in result.classified] == ["shell"]


@pytest.mark.parametrize(
    ("name", "cls"),
    [
        ("MP", "mechanical"), ("mp", "mechanical"), ("MP1", "mechanical"),
        ("MH2", "mechanical"), ("M", "mechanical"), ("SH", "shell"),
        ("Sh10", "shell"), ("SHIELD", "shell"), ("shield", "shell"), ("SHELL", "shell"),
    ],
)
def test_mounting_pad_names(name, cls):
    footprint = (
        b'(footprint "F" (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))'
        b' (pad "' + name.encode() + b'" smd rect (at 2 0) (size 1 1) (layers "F.Cu")))'
    )
    symbol = b'(symbol "P" (symbol "P_1_1" (pin passive line (at 0 0 0) (number "1"))))'
    result = pin_pad.check_bytes(symbol, footprint)
    assert result.verdict == pin_pad.PASS_WITH_WARNINGS
    assert result.mounting_pads_without_pin == (name,)
    assert [f.cls for f in result.classified] == [cls]


@pytest.mark.parametrize("name", ["G", "S", "D", "GND_TAB", "MPX", "SH-1", "M1"])
def test_other_unmatched_pads_still_fail(name):
    """`G`/`S`/`D` are a FET's real pads; a near-miss name is not exempt."""
    footprint = (
        b'(footprint "F" (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))'
        b' (pad "' + name.encode() + b'" smd rect (at 2 0) (size 1 1) (layers "F.Cu")))'
    )
    symbol = b'(symbol "P" (symbol "P_1_1" (pin passive line (at 0 0 0) (number "1"))))'
    result = pin_pad.check_bytes(symbol, footprint)
    assert result.verdict == pin_pad.FAIL
    assert result.pads_without_pin == (name,)


def test_a_symbol_pin_named_mp_matches_its_pad():
    symbol = (
        b'(symbol "P" (symbol "P_1_1" (pin passive line (at 0 0 0) (number "1"))'
        b' (pin passive line (at 0 5 0) (number "MP"))))'
    )
    footprint = (
        b'(footprint "F" (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))'
        b' (pad "MP" smd rect (at 2 0) (size 1 1) (layers "F.Cu")))'
    )
    result = pin_pad.check_bytes(symbol, footprint)
    assert result.verdict == pin_pad.PASS
    assert result.mounting_pads_without_pin == ()


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


# ---------------------------------------------------------------------
# Bounded work on hostile input (security review of #959)
# ---------------------------------------------------------------------


def _pin_text(number: str, x: int, kind: str = "passive") -> str:
    return f'(pin {kind} line (at {x} 0 0) (length 1) (number "{number}"))'


def _symbol_with_units(units: int, styles: int, pins_per: int, common: int) -> bytes:
    """`common` unit-0 pins all numbered "1" at distinct positions, plus
    `units` × `styles` sub-symbols of `pins_per` pins each."""
    parts = ['(symbol "HOSTILE"', '(symbol "HOSTILE_0_0"']
    parts += [_pin_text("1", i) for i in range(common)]
    parts.append(")")
    for unit in range(1, units + 1):
        for style in range(1, styles + 1):
            parts.append(f'(symbol "HOSTILE_{unit}_{style}"')
            parts += [_pin_text(str(n), n) for n in range(2, pins_per + 2)]
            parts.append(")")
    parts.append(")")
    return " ".join(parts).encode()


# ~0.3-0.5 s locally; generous for slow CI runners. The old
# units x styles x pins loop grows past this by orders of magnitude.
_TIME_LIMIT_S = 3.0
_FOOTPRINT = b'(footprint "F" (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu")))'


def _timed(symbol: bytes) -> tuple[pin_pad.PinPadResult, float]:
    import time

    start = time.perf_counter()
    result = pin_pad.check_bytes(symbol, _FOOTPRINT)
    return result, time.perf_counter() - start


def test_symbol_just_inside_the_caps_is_checked_quickly():
    """255 units × 3 styles × 12 pins + 800 duplicated unit-0 pins: the
    old unit × style × pin loop did ~8M pin visits here."""
    symbol = _symbol_with_units(units=255, styles=3, pins_per=12, common=800)
    assert len(symbol) <= 1024 * 1024
    result, elapsed = _timed(symbol)
    assert result.verdict == pin_pad.FAIL
    assert "1" in result.duplicate_pin_numbers
    assert elapsed < _TIME_LIMIT_S, f"{elapsed:.2f}s"


@pytest.mark.parametrize(
    ("units", "styles", "pins_per", "common"),
    [
        (300, 1, 1, 0),  # too many units
        (2, 6, 1, 0),  # too many body styles
        (1, 1, 1, 10_001),  # too many pins
    ],
)
def test_symbol_past_the_caps_is_not_checkable_and_fast(units, styles, pins_per, common):
    symbol = _symbol_with_units(units, styles, pins_per, common)
    result, elapsed = _timed(symbol)
    assert result.verdict == pin_pad.NOT_CHECKABLE
    assert result.reason == pin_pad.REASON_SYMBOL_TOO_COMPLEX
    assert elapsed < _TIME_LIMIT_S, f"{elapsed:.2f}s"


def test_input_caps_match_the_upload_lane():
    from app.domain.eda import storage

    assert pin_pad._MAX_SYMBOL_BYTES == storage.MAX_BYTES_BY_KIND["symbol"]
    assert pin_pad._MAX_FOOTPRINT_BYTES == storage.MAX_BYTES_BY_KIND["footprint"]
    oversized = b" " * (pin_pad._MAX_SYMBOL_BYTES + 1)
    assert pin_pad.check_bytes(oversized, _FOOTPRINT).reason == (
        pin_pad.REASON_SYMBOL_UNREADABLE
    )
    big_fp = b" " * (pin_pad._MAX_FOOTPRINT_BYTES + 1)
    symbol = (FIXTURES / "soic8.kicad_sym").read_bytes()
    assert pin_pad.check_bytes(symbol, big_fp).reason == (
        pin_pad.REASON_FOOTPRINT_UNREADABLE
    )


def test_deep_nesting_is_refused_by_the_parser():
    deep = b'(symbol "D" ' + b"(x " * 100 + b")" * 101
    assert pin_pad.check_bytes(deep, _FOOTPRINT).reason == pin_pad.REASON_SYMBOL_UNREADABLE


# ---------------------------------------------------------------------
# SM-008b: finding classes, on the shapes of the first real run's fails
# ---------------------------------------------------------------------


def _classes(result: pin_pad.PinPadResult) -> dict[str, str]:
    return {f.number: f.cls for f in result.classified if f.kind == "pad_without_pin"}


def test_exposed_pad_and_its_separately_numbered_thermal_vias_are_exposed_pad():
    """THS4541IRGTR: QFN-16 with EP 17 and vias 18-22, none in the symbol."""
    result = _check("qfn16_amp.kicad_sym", "qfn16_ep_vias_numbered.kicad_mod")
    assert result.verdict == pin_pad.PASS_WITH_WARNINGS
    assert _classes(result) == {str(n): "exposed_pad" for n in range(17, 23)}
    # Legacy list keeps its old meaning: every unclaimed electrical pad.
    assert result.pads_without_pin == tuple(str(n) for n in range(17, 23))


_FT232_NC = ("5", "12", "13", "23", "25", "29")


def _mark_pads(footprint: str, numbers, marking: str) -> bytes:
    """Add `marking` (a `(pinfunction …)` / `(pintype …)`) to those pads."""
    for n in numbers:
        footprint = re.sub(
            rf'(\(pad "{n}" [^\n]*?\(layers [^)]*\))', rf"\1 {marking}", footprint
        )
    return footprint.encode()


def test_unmarked_nc_package_pins_fail_but_the_ep_is_a_warning():
    """FT232RQ: QFN-32 EP 33; symbol leaves out NC pins 5/12/13/23/25/29.
    Fail-safe: geometry is no evidence of NC, so the unmarked gaps are
    electrical and the part fails until marked or accepted."""
    result = _check("qfn32_usb_uart.kicad_sym", "qfn32_ep33.kicad_mod")
    assert result.verdict == pin_pad.FAIL
    expected = {n: "electrical" for n in _FT232_NC}
    assert _classes(result) == {**expected, "33": "exposed_pad"}


def test_pads_the_footprint_marks_nc_are_nc_pin():
    symbol = (FIXTURES / "qfn32_usb_uart.kicad_sym").read_bytes()
    footprint = _mark_pads(
        (FIXTURES / "qfn32_ep33.kicad_mod").read_text(), _FT232_NC, '(pinfunction "NC")'
    )
    result = pin_pad.check_bytes(symbol, footprint)
    assert result.verdict == pin_pad.PASS_WITH_WARNINGS
    assert _classes(result) == {**{n: "nc_pin" for n in _FT232_NC}, "33": "exposed_pad"}


@pytest.mark.parametrize(
    ("symbol", "footprint", "numbers"),
    [
        ("sot23_5_ldo.kicad_sym", "sot23_5.kicad_mod", ("4",)),  # TPS7A0518PDBVT
        ("soic16_isolator.kicad_sym", "soic16w.kicad_mod", ("4", "6", "7", "14")),  # ISO1042
    ],
)
def test_unmarked_gap_in_the_symbols_numbering_fails(symbol, footprint, numbers):
    """Indistinguishable from a symbol that forgot GND: electrical."""
    unmarked = _check(symbol, footprint)
    assert unmarked.verdict == pin_pad.FAIL
    assert _classes(unmarked) == {n: "electrical" for n in numbers}
    marked = pin_pad.check_bytes(
        (FIXTURES / symbol).read_bytes(),
        _mark_pads((FIXTURES / footprint).read_text(), numbers, '(pintype "no_connect")'),
    )
    assert marked.verdict == pin_pad.PASS_WITH_WARNINGS
    assert _classes(marked) == {n: "nc_pin" for n in numbers}


def test_tilde_pads_are_mechanical():
    """Würth 68715014022: copper retention pegs numbered `~`."""
    result = _check("wurth_4p.kicad_sym", "wurth_4p_tilde.kicad_mod")
    assert result.verdict == pin_pad.PASS_WITH_WARNINGS
    assert _classes(result) == {"~": "mechanical"}


def test_m_pads_are_mechanical():
    """TC2030-MCP-NL: copper legs named exactly `M`; its NPTH is silent."""
    result = _check("tc2030_6.kicad_sym", "tc2030_mcp_nl.kicad_mod")
    assert result.verdict == pin_pad.PASS_WITH_WARNINGS
    assert _classes(result) == {"M": "mechanical"}
    assert result.mounting_pads_without_pin == ("M",)


def test_connector_shell_pads_by_footprint_words():
    """Molex 73415-1471: centre contact 1, ground shell pads 2-5."""
    result = _check("rf_coax_1.kicad_sym", "molex_73415_rf.kicad_mod")
    assert result.verdict == pin_pad.PASS_WITH_WARNINGS
    assert _classes(result) == {n: "shell" for n in ("2", "3", "4", "5")}


def test_connector_shell_pads_by_part_category_flag():
    symbol = (FIXTURES / "rf_coax_1.kicad_sym").read_bytes()
    footprint = (FIXTURES / "rf_5pad_plain.kicad_mod").read_bytes()
    plain = pin_pad.check_bytes(symbol, footprint)
    filed = pin_pad.check_bytes(symbol, footprint, connector=True)
    # Without connector context the same geometry is NOT excused.
    assert plain.verdict == pin_pad.FAIL
    assert set(_classes(plain).values()) == {"electrical"}
    assert filed.verdict == pin_pad.PASS_WITH_WARNINGS
    assert set(_classes(filed).values()) == {"shell"}


@pytest.mark.parametrize(
    ("symbol", "footprint", "number", "connector"),
    [
        # A header symbol missing its last pin: pad 4 is IN the row.
        ("header_3.kicad_sym", "pinheader_1x04.kicad_mod", "4", True),
        # A DPAK symbol missing the drain tab: large and at the edge,
        # but not a connector — a real open.
        ("dpak_fet.kicad_sym", "dpak_tab4.kicad_mod", "4", False),
        # A signal-sized pad 9 in the middle of an SOIC-8 (SM-008 acceptance).
        ("soic8.kicad_sym", "soic8_pad9.kicad_mod", "9", False),
    ],
)
def test_conservative_cases_stay_electrical(symbol, footprint, number, connector):
    result = pin_pad.check_bytes(
        (FIXTURES / symbol).read_bytes(),
        (FIXTURES / footprint).read_bytes(),
        connector=connector,
    )
    assert result.verdict == pin_pad.FAIL
    assert _classes(result) == {number: "electrical"}


def test_pins_without_pad_and_duplicates_are_always_electrical():
    missing = _check("soic8.kicad_sym", "soic8_no_pad5.kicad_mod")
    dupes = _check("dup_pins.kicad_sym", "soic8_mech.kicad_mod")
    assert [f.as_dict() for f in missing.classified] == [
        {"kind": "pin_without_pad", "number": "5", "class": "electrical",
         "reason": "symbol pin has no footprint pad"}
    ]
    assert [(f.kind, f.number, f.cls) for f in dupes.classified] == [
        ("duplicate_pin", "3", "electrical")
    ]
    # A no_connect pin without a pad stays information, not a finding.
    nc = _check("nc_pin.kicad_sym", "soic8_no_pad5.kicad_mod")
    assert nc.verdict == pin_pad.PASS and nc.classified == ()


def test_verdict_for_sets_accepted_findings_aside():
    warn = pin_pad.Finding("pad_without_pin", "33", "exposed_pad", "r")
    bad = pin_pad.Finding("pad_without_pin", "9", "electrical", "r")
    assert pin_pad.verdict_for((warn, bad)) == pin_pad.FAIL
    assert pin_pad.verdict_for((warn, bad), {bad.key}) == pin_pad.PASS_WITH_WARNINGS
    assert pin_pad.verdict_for((warn, bad), {bad.key, warn.key}) == pin_pad.PASS
    assert pin_pad.verdict_for(()) == pin_pad.PASS


def test_connector_cache_key_is_separate():
    symbol = (FIXTURES / "rf_coax_1.kicad_sym").read_bytes()
    footprint = (FIXTURES / "rf_5pad_plain.kicad_mod").read_bytes()
    plain = pin_pad.check_cached("sym-conn", "fp-conn", lambda: (symbol, footprint))
    filed = pin_pad.check_cached(
        "sym-conn", "fp-conn", lambda: (symbol, footprint), connector=True
    )
    assert (plain.verdict, filed.verdict) == (pin_pad.FAIL, pin_pad.PASS_WITH_WARNINGS)


def test_rotated_pad_bounds_and_fab_fallback_body():
    from app.domain.eda import pin_pad_classify as c

    node = c.sexpr.parse(
        '(footprint "X" (fp_line (start -2 -1) (end 2 1) (layer "F.Fab"))'
        ' (pad "1" smd rect (at 0 0 90) (size 2 1) (layers "F.Cu")))'
    )
    geo = c.footprint_geometry(node)
    assert geo.body == c.Box(-2, -1, 2, 1)
    box = geo.pads[0].box
    assert (round(box.w, 6), round(box.h, 6)) == (1.0, 2.0)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Molex_MCX_73415", True), ("USB_C_Receptacle", True), ("Conn_01x04", True),
        ("Through hole straight pin header", True), ("Connectors", True),
        ("RF Jacks", True), ("SOIC-8", False),
        ("QFN-32 exposed pad", False), ("Jackson", False), (None, False),
    ],
)
def test_connector_words(text, expected):
    from app.domain.eda.pin_pad_classify import is_connector_text

    assert is_connector_text(text) is expected


# ---------------------------------------------------------------------
# SM-008b review: unsure geometry is electrical, and the work is bounded
# ---------------------------------------------------------------------

_ONE_PIN = b'(symbol "P" (symbol "P_1_1" (pin passive line (at 0 0 0) (number "1"))))'


def _pads(*pads: str, name: str = "F") -> bytes:
    return f'(footprint "{name}" {" ".join(pads)})'.encode()


def _smd(number: str, x: float, y: float, w: float = 1, h: float = 1, extra: str = "") -> str:
    return (
        f'(pad "{number}" smd rect (at {x} {y}) (size {w} {h}) (layers "F.Cu"){extra})'
    )


def test_more_pads_than_the_cap_is_not_checkable_and_fast():
    pads = [_smd(str(n), n % 100, n // 100) for n in range(1, pin_pad.MAX_PADS + 2)]
    result, elapsed = _timed_pair(_ONE_PIN, _pads(*pads))
    assert result.verdict == pin_pad.NOT_CHECKABLE
    assert result.reason == pin_pad.REASON_FOOTPRINT_TOO_COMPLEX
    assert elapsed < _TIME_LIMIT_S, f"{elapsed:.2f}s"


def test_pathological_2mb_footprint_returns_quickly():
    """The largest footprint the upload lane accepts, packed with tiny
    distinct pads plus large central ones (the E3 containment scan): it
    must be refused or classified in bounded time, never pads x pads."""
    pads = [_smd("EP", 0, 0, 50, 50)] * 20
    size, n = sum(len(p) + 1 for p in pads), 1
    while size < pin_pad._MAX_FOOTPRINT_BYTES - 200:
        pad = _smd(str(n), (n % 40) - 20, (n // 40) % 40 - 20, 0.1, 0.1)
        pads.append(pad)
        size, n = size + len(pad) + 1, n + 1
    footprint = _pads(*pads)
    assert len(footprint) <= pin_pad._MAX_FOOTPRINT_BYTES
    result, elapsed = _timed_pair(_ONE_PIN, footprint)
    assert result.verdict == pin_pad.NOT_CHECKABLE
    assert elapsed < _TIME_LIMIT_S, f"{elapsed:.2f}s"


def test_many_distinct_pads_under_the_cap_classify_in_bounded_time():
    """The old per-number scan of every pad was O(pads²)."""
    pads = [_smd(str(n), n % 70, n // 70, 0.5, 0.5) for n in range(1, 4_900)]
    pads.append(_smd("EP", 35, 35, 10, 10))
    result, elapsed = _timed_pair(_ONE_PIN, _pads(*pads))
    assert result.verdict in (pin_pad.FAIL, pin_pad.PASS_WITH_WARNINGS)
    assert elapsed < _TIME_LIMIT_S, f"{elapsed:.2f}s"


def _timed_pair(symbol: bytes, footprint: bytes) -> tuple[pin_pad.PinPadResult, float]:
    import time

    start = time.perf_counter()
    result = pin_pad.check_bytes(symbol, footprint)
    return result, time.perf_counter() - start


def test_zero_size_or_non_finite_pads_are_electrical():
    symbol = (FIXTURES / "sot23_5_ldo.kicad_sym").read_bytes()
    footprint = (FIXTURES / "sot23_5.kicad_mod").read_text()
    zero = footprint.replace(
        '(pad "4" smd roundrect (at 1.1 0.95) (size 1.06 0.65)',
        '(pad "4" smd roundrect (at 1.1 0.95) (size 0 0)',
    )
    nan = footprint.replace("(size 1.06 0.65)", "(size nan 0.65)")
    for text in (zero, nan):
        result = pin_pad.check_bytes(symbol, text.encode())
        assert _classes(result) == {"4": "electrical"}, text


@pytest.mark.parametrize(
    ("marking", "cls"),
    [
        ('(pinfunction "GND")', "electrical"),
        ('(pinfunction "NC")', "nc_pin"),
        ('(pinfunction "n.c.")', "nc_pin"),
        ('(pinfunction "NC_3")', "nc_pin"),
        ('(pinfunction "DNC")', "nc_pin"),
        ('(pinfunction "nCS")', "electrical"),
        ('(pinfunction "NCLK")', "electrical"),
        ('(pintype "no_connect")', "nc_pin"),
        ('(pintype "passive+no_connect")', "nc_pin"),
        ('(pintype "passive")', "electrical"),
        ("", "electrical"),
    ],
)
def test_only_positive_nc_evidence_makes_nc_pin(marking, cls):
    symbol = (FIXTURES / "sot23_5_ldo.kicad_sym").read_bytes()
    footprint = (FIXTURES / "sot23_5.kicad_mod").read_text().replace(
        '(pad "4" smd roundrect (at 1.1 0.95) (size 1.06 0.65)',
        f'(pad "4" smd roundrect (at 1.1 0.95) (size 1.06 0.65) {marking}',
    )
    assert _classes(pin_pad.check_bytes(symbol, footprint.encode())) == {"4": cls}


def test_a_number_is_nc_only_if_every_pad_of_it_is_marked():
    symbol = (FIXTURES / "sot23_5_ldo.kicad_sym").read_bytes()
    footprint = (FIXTURES / "sot23_5.kicad_mod").read_text().replace(
        '(pad "4" smd roundrect (at 1.1 0.95) (size 1.06 0.65)',
        '(pad "4" smd rect (at 1.1 1.5) (size 0.3 0.3) (layers "F.Cu"))'
        ' (pad "4" smd roundrect (at 1.1 0.95) (size 1.06 0.65) (pinfunction "NC")',
    )
    assert _classes(pin_pad.check_bytes(symbol, footprint.encode())) == {"4": "electrical"}


def test_signal_sized_pads_off_the_row_of_a_connector_stay_electrical():
    """A USB-C symbol that forgot the B row: those pads are off the A
    row but signal-sized, so they are a real mismatch, not a shell."""
    a_row = [_smd(f"A{i}", i * 0.5, -3) for i in range(1, 13)]
    b_row = [_smd(f"B{i}", i * 0.5, -1.5) for i in range(1, 13)]
    shells = [_smd("S1", -1.5, -2, 2, 2), _smd("S1", 8, -2, 2, 2)]
    footprint = _pads(*a_row, *b_row, *shells, name="USB_C_Receptacle")
    pins = " ".join(
        f'(pin passive line (at 0 {i} 0) (number "A{i}"))' for i in range(1, 13)
    )
    symbol = f'(symbol "U" (symbol "U_1_1" {pins}))'.encode()
    result = pin_pad.check_bytes(symbol, footprint)
    classes = _classes(result)
    assert {classes[f"B{i}"] for i in range(1, 13)} == {"electrical"}
    assert classes["S1"] == "shell"
    assert result.verdict == pin_pad.FAIL
