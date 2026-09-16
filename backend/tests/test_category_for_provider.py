"""A4 — provider category strings onto OUR category name paths.

`spec_schema.category_for_provider` is the rule table import and refresh
consult before they touch `parts.category_id`. It is pure: no DB, no
workspace, no I/O. Resolving the returned path against a workspace's own
tree is `categories/service.py::resolve_category_path`, tested in
`test_spec_reconcile.py`.

The names below are the real DigiKey `Category.Name` and Mouser
`Category` strings named in the plan, kept verbatim — a rule table is
only worth having if it is pinned to the strings it was written for.
"""
from __future__ import annotations

import pytest

from app.domain.parts.spec_schema import category_for_provider

# (DigiKey category name, our path)
DIGIKEY_CASES = [
    ("Chip Resistor - Surface Mount", "Resistors"),
    ("Through Hole Resistors", "Resistors"),
    ("Ceramic Capacitors", "Capacitors / Ceramic"),
    ("Aluminum Electrolytic Capacitors", "Capacitors / Electrolytic"),
    ("Tantalum Capacitors", "Capacitors / Tantalum"),
    ("Film Capacitors", "Capacitors / Film"),
    ("Fixed Inductors", "Inductors"),
    ("Diodes - Rectifiers - Single", "Diodes"),
    ("Diodes - Rectifiers - Arrays", None),
    ("Diodes - Zener - Single", "Diodes / Zener"),
    ("TVS - Diodes", "Diodes / TVS"),
    ("LED Indication - Discrete", "Diodes / LED"),
    ("Transistors - Bipolar (BJT) - Single", "Transistors / BJT"),
    ("Transistors - FETs, MOSFETs - Single", "Transistors / MOSFET"),
]

# (Mouser category name, our path)
MOUSER_CASES = [
    ("Thick Film Resistors - SMD", "Resistors"),
    ("Multilayer Ceramic Capacitors MLCC - SMD/SMT", "Capacitors / Ceramic"),
    ("Aluminum Electrolytic Capacitors - SMD", "Capacitors / Electrolytic"),
    ("Tantalum Capacitors", "Capacitors / Tantalum"),
    ("Fixed Inductors", "Inductors"),
    ("Rectifiers", "Diodes"),
    ("Zener Diodes", "Diodes / Zener"),
    ("TVS Diodes", "Diodes / TVS"),
    ("Standard LEDs - SMD", "Diodes / LED"),
    ("Bipolar Transistors - BJT", "Transistors / BJT"),
    ("MOSFET", "Transistors / MOSFET"),
]


@pytest.mark.parametrize(("provider_category", "expected"), DIGIKEY_CASES)
def test_digikey_category_names_map_to_our_tree(provider_category, expected):
    assert category_for_provider("digikey", provider_category) == expected


@pytest.mark.parametrize(("provider_category", "expected"), MOUSER_CASES)
def test_mouser_category_names_map_to_our_tree(provider_category, expected):
    assert category_for_provider("mouser", provider_category) == expected


def test_a_bare_class_noun_is_ambiguous_and_maps_to_nothing():
    """A dielectric changes the whole spec set, so "Capacitors" alone is
    not a category decision — it is a missing one. Same for a bare
    "Transistors", where the channel type is the spec set."""
    assert category_for_provider("digikey", "Capacitors") is None
    assert category_for_provider("digikey", "Transistors") is None


@pytest.mark.parametrize(
    "provider_category",
    [
        "Resistor Networks, Arrays",
        "Capacitor Networks, Arrays",
        "Resistor Kits",
        "LED Assortment Kits",
    ],
)
def test_networks_arrays_and_kits_are_not_the_single_component(provider_category):
    """A resistor array is not a resistor: it has no single resistance,
    and filing it under Resistors would make every mandatory key on it
    permanently missing."""
    assert category_for_provider("digikey", provider_category) is None


@pytest.mark.parametrize(
    "provider_category",
    [
        "Ceramic Resonators",
        "Thermistors - NTC",
        "Trimmer Potentiometers",
        "Solid State Relays",
        "Optoisolators - Transistor, Photovoltaic Output",
        "Thyristors - SCRs",
        "Interface - Analog Switches",
    ],
)
def test_a_category_outside_the_schema_maps_to_nothing(provider_category):
    """No guess is the right answer: a part we cannot classify keeps a
    NULL category and shows up as a suggestion, not a wrong filing."""
    assert category_for_provider("digikey", provider_category) is None


def test_an_adjective_never_decides_the_class():
    """"Thick Film Resistors" is a resistor, not a film capacitor —
    the same two-pass rule ADR-0034 pins for `category_slug_for`."""
    assert category_for_provider("mouser", "Thick Film Resistors - SMD") == "Resistors"
    assert category_for_provider("mouser", "Ceramic Resonators") is None


def test_mouser_falls_back_to_the_description():
    """Mouser's `Category` is coarse and often absent; its description
    carries the class ("Thick Film Resistors - SMD General Purpose Chip
    Resistor 0402, 0Ohms, 5%, 1/16W"). `providers/mouser.py` already
    mines that string for specs — this is the same source."""
    assert (
        category_for_provider(
            "mouser",
            None,
            "Thick Film Resistors - SMD General Purpose Chip Resistor 0402, 0Ohms, 5%",
        )
        == "Resistors"
    )


def test_digikey_does_not_fall_back_to_the_description():
    """DigiKey's category IS the leaf of its taxonomy, and its
    description is a terse part string ("RES SMD 10K OHM 1% 1/16W 0402")
    that names no class. Mining it would only add false positives."""
    assert (
        category_for_provider("digikey", None, "RES SMD 10K OHM 1% 1/16W 0402") is None
    )


def test_the_provider_category_beats_the_description():
    """A real taxonomy entry outranks prose, whoever sent it."""
    assert (
        category_for_provider(
            "mouser", "Zener Diodes", "Thick Film Resistors - SMD 0402"
        )
        == "Diodes / Zener"
    )


@pytest.mark.parametrize("text", [None, "", "   ", "-"])
def test_empty_input_is_no_category(text):
    assert category_for_provider("digikey", text) is None
    assert category_for_provider("mouser", text, text) is None


def test_an_unknown_provider_still_reads_its_category():
    """The rule table is a taxonomy map, not a credential check — a
    provider name we don't know loses only the Mouser description
    fallback, not the category itself."""
    assert category_for_provider("lcsc", "Zener Diodes") == "Diodes / Zener"
    assert category_for_provider(None, "Zener Diodes") == "Diodes / Zener"


def test_every_returned_path_round_trips_to_a_schema_slug():
    """The path this returns is fed back through `category_slug_for` once
    the category resolves, so a path that does not classify would leave
    the part with the common schema only — silently, and for every part
    the rule matched."""
    from app.domain.parts.spec_schema import category_slug_for

    paths = {p for _, p in DIGIKEY_CASES + MOUSER_CASES if p is not None}
    unclassifiable = sorted(p for p in paths if category_slug_for(p) is None)
    assert unclassifiable == []
