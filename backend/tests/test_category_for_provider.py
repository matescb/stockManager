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
    # The seven roots added with the active-component schema.
    ("Integrated Circuits (ICs)", "ICs"),
    ("Embedded - Microcontrollers", "ICs"),
    ("Embedded - FPGAs (Field Programmable Gate Array)", "ICs"),
    ("Logic - Gates and Inverters", "ICs"),
    ("Interface - Analog Switches, Multiplexers, Demultiplexers", "ICs"),
    ("PMIC - Voltage Regulators - Linear", "ICs"),
    ("Linear - Amplifiers - Instrumentation, OP Amps, Buffer Amps", "ICs"),
    ("Data Acquisition - Analog to Digital Converters (ADC)", "ICs"),
    ("Clock/Timing - Clock Generators, PLLs, Frequency Synthesizers", "ICs"),
    ("Memory", "ICs"),
    ("Connectors, Interconnects", "Connectors"),
    ("Rectangular Connectors - Headers, Male Pins", "Connectors"),
    ("Terminal Blocks - Headers, Plugs and Sockets", "Connectors"),
    ("USB, DVI, HDMI Connectors", "Connectors"),
    ("Circular Connectors", "Connectors"),
    ("Card Edge Connectors - Edge Board Connectors", "Connectors"),
    ("FFC, FPC (Flat Flexible) Connectors", "Connectors"),
    ("Pluggable Connectors", "Connectors"),
    ("Crystals", "Crystals & Oscillators"),
    ("Oscillators", "Crystals & Oscillators"),
    ("Resonators", "Crystals & Oscillators"),
    ("Crystals, Oscillators, Resonators - Accessories", "Crystals & Oscillators"),
    ("Fuses", "Fuses"),
    ("PTC Resettable Fuses", "Fuses"),
    ("Circuit Protection - Fuses and Accessories", "Fuses"),
    ("Tactile Switches", "Switches"),
    ("Slide Switches", "Switches"),
    ("DIP Switches", "Switches"),
    ("Rocker Switches", "Switches"),
    ("Toggle Switches", "Switches"),
    ("Switches", "Switches"),
    ("Transformers - Pulse", "Transformers"),
    ("Power Transformers", "Transformers"),
    ("Pulse Transformers", "Transformers"),
    ("Audio Transformers", "Transformers"),
    ("Hardware, Fasteners, Accessories - Standoffs and Spacers", "Mechanical"),
    ("Standoffs", "Mechanical"),
    ("Screws and Bolts", "Mechanical"),
    ("Heat Sinks", "Mechanical"),
    ("Enclosures, Boxes, Cases", "Mechanical"),
    ("Cable Ties and Zip Ties", "Mechanical"),
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
    ("Integrated Circuits - ICs", "ICs"),
    ("Semiconductors > Integrated Circuits - ICs", "ICs"),
    ("Microcontrollers - MCU", "ICs"),
    ("Amplifier ICs - Audio Amplifiers", "ICs"),
    ("Logic ICs - Gates", "ICs"),
    ("Interface ICs - Transceivers", "ICs"),
    ("Power Management ICs - Voltage Regulators", "ICs"),
    ("Memory ICs - EEPROM", "ICs"),
    ("Clock & Timer ICs - Real Time Clock", "ICs"),
    ("Connectors", "Connectors"),
    ("Headers & Wire Housings", "Connectors"),
    ("Terminal Blocks - Headers", "Connectors"),
    ("USB Connectors", "Connectors"),
    ("Circular Connectors - Accessories", "Connectors"),
    ("FFC/FPC Connectors", "Connectors"),
    ("Crystals", "Crystals & Oscillators"),
    ("Oscillators - Clock Oscillators", "Crystals & Oscillators"),
    ("Resonators", "Crystals & Oscillators"),
    ("Frequency Control Circuits", "Crystals & Oscillators"),
    ("Fuses - Cartridge", "Fuses"),
    ("Resettable Fuses - PPTC", "Fuses"),
    ("Circuit Protection > Fuses", "Fuses"),
    ("Tactile Switches", "Switches"),
    ("Slide Switches", "Switches"),
    ("DIP Switches", "Switches"),
    ("Switches - Pushbutton", "Switches"),
    ("Transformers - Audio", "Transformers"),
    ("Hardware - Standoffs", "Mechanical"),
    ("Standoffs & Spacers", "Mechanical"),
    ("Screws & Fasteners", "Mechanical"),
    ("Heat Sinks", "Mechanical"),
    ("Enclosures - Boxes", "Mechanical"),
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
        "Thermistors - NTC",
        "Trimmer Potentiometers",
        "Solid State Relays",
        "Optoisolators - Transistor, Photovoltaic Output",
        "Thyristors - SCRs",
        "Motors - AC, DC",
        "Batteries Non-Rechargeable (Primary)",
    ],
)
def test_a_category_outside_the_schema_maps_to_nothing(provider_category):
    """No guess is the right answer: a part we cannot classify keeps a
    NULL category and shows up as a suggestion, not a wrong filing."""
    assert category_for_provider("digikey", provider_category) is None


def test_an_adjective_never_decides_the_class():
    """"Thick Film Resistors" is a resistor, not a film capacitor —
    the same two-pass rule ADR-0034 pins for `category_slug_for`. A
    ceramic resonator is a resonator for the same reason."""
    assert category_for_provider("mouser", "Thick Film Resistors - SMD") == "Resistors"
    assert (
        category_for_provider("mouser", "Ceramic Resonators")
        == "Crystals & Oscillators"
    )


@pytest.mark.parametrize(
    ("provider_category", "expected"),
    [
        # A vendor category naming two classes is decided by CLASS_RULES
        # order, not by which word comes first in the string. These are the
        # three overlaps the seven new classes introduced.
        ("Interface - Analog Switches, Multiplexers", "ICs"),
        ("Clock/Timing - Clock Generators, PLLs, Frequency Synthesizers", "ICs"),
        ("Oscillators - Clock Oscillators", "Crystals & Oscillators"),
    ],
)
def test_a_category_naming_two_classes_resolves_the_documented_way(
    provider_category, expected
):
    assert category_for_provider("digikey", provider_category) == expected


@pytest.mark.parametrize(
    "provider_category",
    ["Resistors", "Chip Resistor - Surface Mount", "Thick Film Resistors - SMD"],
)
def test_the_passive_classes_did_not_move(provider_category):
    """Seven new classes, none of which may capture a resistor."""
    assert category_for_provider("digikey", provider_category) == "Resistors"


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


@pytest.mark.parametrize(
    "provider_category",
    [
        "Embedded - FPGAs (Field Programmable Gate Array)",
        "Logic - Buffers, Drivers, Receivers, Transceivers",
        "Interface - Analog Switches, Multiplexers",
    ],
)
def test_an_ic_keeps_its_class_through_the_word_array(provider_category):
    """"Several of these in one package" breaks a passive's spec set — a
    4-way resistor array has four resistances. It does not break an IC's:
    one supply voltage, one function. DigiKey's own name for the whole
    FPGA family is "… (Field Programmable Gate Array)", so applying the
    guard there would refuse the family over a parenthetical."""
    assert category_for_provider("digikey", provider_category) == "ICs"


@pytest.mark.parametrize(
    "provider_category",
    ["Integrated Circuit Kits", "Microcontroller Assortment", "Logic IC Kits"],
)
def test_an_assortment_is_not_a_part_of_any_class(provider_category):
    """A bag of assorted parts is not one part, whatever class the bag is
    labelled with — so this guard, unlike the array one, has no exemption."""
    assert category_for_provider("digikey", provider_category) is None


# ---------------------------------------------------------------------------
# Thermal management
#
# "Thermal Interface Materials" read as an IC, because `interface` is an IC
# trigger. A thermal pad is hardware, so `thermal` gets a rule of its own
# ahead of the IC vocabulary — and behind the two classes whose own nouns
# beat it.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "provider_category",
    [
        "Thermal Interface Materials",
        "Thermal - Pads, Sheets",
        "Thermal - Heat Sinks",
        "Thermal - Thermoelectric, Peltier Modules",
    ],
)
def test_digikey_thermal_products_are_mechanical(provider_category):
    assert category_for_provider("digikey", provider_category) == "Mechanical"


@pytest.mark.parametrize(
    "provider_category",
    [
        "Thermal Management",
        "Thermal Management - Heat Sinks",
        "Thermal Management > Thermal Interface Products",
    ],
)
def test_mouser_thermal_management_is_mechanical(provider_category):
    assert category_for_provider("mouser", provider_category) == "Mechanical"


@pytest.mark.parametrize(
    ("provider_category", "expected"),
    [
        # A category that says "IC" outright is an IC whatever else it
        # names — which is why those words sit AHEAD of the thermal rule.
        ("PMIC - Thermal Management", "ICs"),
        ("Thermal Management ICs", "ICs"),
        # And a thermal fuse is a fuse: `fuse` sits ahead of `thermal` too.
        ("Thermal Cutoffs (Thermal Fuses)", "Fuses"),
    ],
)
def test_thermal_does_not_capture_a_class_that_names_itself(
    provider_category, expected
):
    assert category_for_provider("digikey", provider_category) == expected


@pytest.mark.parametrize(
    "provider_category",
    [
        "Memory Connectors - PC Card Sockets",
        "Memory Connectors - Inline Module Sockets",
        "Card Edge Connectors - Memory",
    ],
)
def test_a_memory_connector_is_a_connector(provider_category):
    """"Memory" is in the weaker half of the IC vocabulary and a socket is
    not an IC, so the connector rule comes first. It is safe there: no IC
    category names a connector."""
    assert category_for_provider("digikey", provider_category) == "Connectors"


@pytest.mark.parametrize(
    ("provider_category", "expected"),
    [
        ("Interface ICs - Transceivers", "ICs"),
        ("Memory ICs - EEPROM", "ICs"),
        ("Logic ICs - Gates", "ICs"),
    ],
)
def test_putting_connectors_first_does_not_cost_the_ic_categories(
    provider_category, expected
):
    assert category_for_provider("mouser", provider_category) == expected
