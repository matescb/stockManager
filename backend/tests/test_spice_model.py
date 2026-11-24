"""SM-009 parser + pin-map checks — pure functions, no database.

Fixtures live in `tests/fixtures/spice/`. The `*_ng010.lib` files carry
the header shape the ngSPICE model library's `bin/export-model` writes
(`tools/export_model.py::header`) with SYNTHETIC values and bodies — no
vendor model text is copied into this repo. The real exports are checked
in place by `test_spice_inventory_exports.py`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.domain.eda import sexpr, spice_model
from app.domain.eda.spice_binding import _symbol_pin_numbers, check_pin_map, sorted_pins

FIXTURES = Path(__file__).parent / "fixtures" / "spice"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _code(exc: pytest.ExceptionInfo) -> str:
    return exc.value.detail["code"]


# ---------------------------------------------------------------------
# .subckt / .model
# ---------------------------------------------------------------------


def test_subckt_with_continuations_params_and_comments():
    models = spice_model.parse_models(_read("opa192_continuation.lib"))
    # The nested OPA192_INNER and the inner .MODEL are not top-level.
    assert [m.name for m in models] == ["OPA192"]
    opa = models[0]
    assert opa.kind == "subckt"
    assert opa.ports == ("IN+", "IN-", "V+", "V-", "OUT")
    assert opa.params == ("GAIN", "IOS", "VOS")
    assert opa.checkable


def test_single_line_acceptance_example():
    models = spice_model.parse_models(".subckt OPA192 IN+ IN- V+ V- OUT\n.ends\n")
    assert models[0].name == "OPA192"
    assert models[0].ports == ("IN+", "IN-", "V+", "V-", "OUT")


def test_multi_subckt_file_and_ltspice_defaults_without_params_keyword():
    models = spice_model.parse_models(_read("two_opamps.lib"))
    assert [m.name for m in models] == ["OPA_A", "OPA_B"]
    assert models[0].ports == ("inp", "inn", "vcc", "vee", "out")
    assert models[0].params == ("Rload",)
    assert models[1].ports == ("1", "2", "3", "4", "5")
    assert spice_model.find_model(models, "opa_b") is models[1]


def test_params_glued_to_value():
    models = spice_model.parse_models(".SUBCKT X a b PARAMS:R=1k\n.ENDS\n")
    assert models[0].ports == ("a", "b")
    assert models[0].params == ("R",)


def test_model_only_file():
    models = spice_model.parse_models(_read("models_only.lib"))
    assert [(m.name, m.device_type) for m in models] == [("DSIMPLE", "D"), ("RMOD", "R")]
    assert models[0].ports == ("A", "K")
    assert not models[1].checkable  # resistor card: no known pin names


@pytest.mark.parametrize(
    ("card", "ports"),
    [
        (".model Q1 NPN(BF=100)", ("C", "B", "E")),
        (".model Q2 pnp", ("C", "B", "E")),
        (".model M1 VDMOS(Rd=1m)", ("D", "G", "S")),
        (".model M2 NMOS (LEVEL=1)", ("D", "G", "S")),
        (".model J1 NJF(Beta=1m)", ("D", "G", "S")),
        (".model S1 SW(Ron=1)", ()),
    ],
)
def test_primitive_ports(card, ports):
    assert spice_model.parse_models(card)[0].ports == ports


def test_empty_and_comment_only_files_define_nothing():
    assert spice_model.parse_models("") == []
    assert spice_model.parse_models("* nothing\n; nor this\n") == []


# ---------------------------------------------------------------------
# NG-010 header
# ---------------------------------------------------------------------


def test_ng010_header_subckt():
    text = _read("tlv431_ng010.lib")
    header = spice_model.parse_ng010_header(text)
    assert header is not None
    assert header.catalogue_entry == "TLV431"
    assert header.catalogue_kind == "subcircuit"
    assert header.manufacturer == "Analog Devices"
    assert header.source == "lib/sub/TLV431.sub"
    assert header.source_sha256 == "1" * 64
    assert header.converted == (
        ("sub/TLV431.sub", "2" * 64),
    )
    assert header.build_requirement.startswith("needs stock; ngbehavior=ltpsa required: True")
    assert header.build_needs == "stock"
    assert header.grade == "valid_for=['dc'] builds=['ngspice-42']"
    assert header.grade_limits == ("synthetic limit line for the parser test",)
    assert header.pins == ("K", "A", "R")
    assert "redistribution terms not established" in header.licence
    assert spice_model.model_source_text(header).startswith(
        "ngspice-library:TLV431; lib/sub/TLV431.sub sha256 1111"
    )
    # The body still parses: header pins are names, ports are node numbers.
    assert spice_model.parse_models(text)[0].ports == ("1", "2", "3")


def test_ng010_header_model_card():
    text = _read("d1n4148_ng010.lib")
    header = spice_model.parse_ng010_header(text)
    assert header.pins == ("A", "K")
    assert spice_model.parse_models(text)[0].ports == ("A", "K")


def test_ng010_header_runtime_build_and_correction():
    header = spice_model.parse_ng010_header(_read("bead_corrected_runtime_ng010.lib"))
    assert header.build_needs == "patched+runtime"
    assert header.manufacturer is None  # "not established"
    assert header.corrections == (
        "NG009-001 (proposed): Rser 3 -> 3m; APPLIED in this file; "
        "see corrections/NG009-001.yaml",
    )
    assert "correction NG009-001 (proposed)" in spice_model.model_source_text(header)


def test_vendor_file_is_not_an_ng010_export():
    assert spice_model.parse_ng010_header(_read("opa192_continuation.lib")) is None


# ---------------------------------------------------------------------
# Sim.Pins
# ---------------------------------------------------------------------


def test_sim_pins_round_trip():
    pairs = spice_model.parse_sim_pins("1=IN+ 2=IN-  3=OUT")
    assert pairs == [("1", "IN+"), ("2", "IN-"), ("3", "OUT")]
    assert spice_model.format_sim_pins(pairs) == "1=IN+ 2=IN- 3=OUT"
    assert spice_model.parse_sim_pins("1 2 3") is None
    assert spice_model.parse_sim_pins("") is None


def test_hosted_symbol_pins_across_units_in_natural_order():
    node = sexpr.entries(_read("opa192.kicad_sym"))[0][1]
    pins = _symbol_pin_numbers(node)
    assert set(pins) == {"1", "2", "3", "4", "5"}
    assert sorted_pins(["10", "2", "1", "A1", "A10", "A2"]) == ["1", "2", "10", "A1", "A2", "A10"]


# ---------------------------------------------------------------------
# check_pin_map refusals — stable codes
# ---------------------------------------------------------------------

PINS = ["1", "2", "3", "4", "5"]
PORTS = ("IN+", "IN-", "V+", "V-", "OUT")
GOOD = {"1": "OUT", "2": "V-", "3": "IN+", "4": "IN-", "5": "V+"}


def test_valid_map_yields_pairs_in_symbol_pin_order():
    pairs = check_pin_map({**GOOD, "3": "in+"}, symbol_pins=PINS, ports=PORTS)
    assert spice_model.format_sim_pins(pairs) == "1=OUT 2=V- 3=IN+ 4=IN- 5=V+"


@pytest.mark.parametrize(
    ("pin_map", "code"),
    [
        ({k: v for k, v in GOOD.items() if k != "5"}, "eda.spice_pin_unmapped"),
        ({**GOOD, "6": "OUT"}, "eda.spice_pin_unknown"),
        ({**GOOD, "5": "VCC"}, "eda.spice_port_unknown"),
        ({**GOOD, "5": "OUT"}, "eda.spice_port_duplicate"),
        ({**GOOD, "5": None}, "eda.spice_port_count_mismatch"),
    ],
)
def test_bad_maps_are_refused_with_stable_codes(pin_map, code):
    with pytest.raises(HTTPException) as exc:
        check_pin_map(pin_map, symbol_pins=PINS, ports=PORTS)
    assert _code(exc) == code


def test_unconnected_ports_must_be_named_explicitly():
    ports = ("1", "2", "3")
    with pytest.raises(HTTPException) as exc:
        check_pin_map({"1": "1", "2": "2"}, symbol_pins=["1", "2"], ports=ports)
    assert _code(exc) == "eda.spice_port_count_mismatch"
    pairs = check_pin_map(
        {"1": "1", "2": "2"}, symbol_pins=["1", "2"], ports=ports, unconnected=["3"]
    )
    assert spice_model.format_sim_pins(pairs) == "1=1 2=2"
    with pytest.raises(HTTPException) as exc:
        check_pin_map(
            {"1": "1", "2": "2"}, symbol_pins=["1", "2"], ports=ports, unconnected=["2", "3"]
        )
    assert _code(exc) == "eda.spice_port_duplicate"
    with pytest.raises(HTTPException) as exc:
        check_pin_map(
            {"1": "1", "2": "2"}, symbol_pins=["1", "2"], ports=ports, unconnected=["9"]
        )
    assert _code(exc) == "eda.spice_port_unknown"


def test_nc_pin_is_left_out_of_sim_pins():
    pairs = check_pin_map(
        {"1": "A", "2": "K", "3": "NC"}, symbol_pins=["1", "2", "3"], ports=("A", "K")
    )
    assert spice_model.format_sim_pins(pairs) == "1=A 2=K"
