"""Spec-schema data, part two: the common optional keys and the active
component classes.

A sibling of `spec_schema_tables.py` rather than more of it, for the
reason that file already gives: pure data, no imports from the rest of
the app, and small enough to review as a diff. `spec_schema_tables.py`
merges what is here into `COMMON_SPECS` and `CANONICAL_SPECS`, and every
rule in its module docstring — alias order is precedence order,
`digikey_aliases` are verbatim `ParameterText` strings — applies here
unchanged.

See ADR-0034.
"""
from __future__ import annotations

from app.domain.parts.spec_key import SpecKey

# ---------------------------------------------------------------------------
# Common optional keys — merged into EVERY category on top of
# `package` / `mounting` / `operating_temp`.
#
# None of them is mandatory: a resistor with no published height is not an
# incomplete resistor. What they buy is a sortable column on the classes
# the schema models least well — a connector's pitch and pin count, an
# IC's package height — and, for `automotive`, one fact out of a key that
# was being thrown away whole.
# ---------------------------------------------------------------------------
EXTRA_COMMON_SPECS: tuple[SpecKey, ...] = (
    SpecKey(
        "height", "m", "Height", False,
        ("Height - Seated (Max)", "Height (Max)"),
        ("Height",),
        extract="metric",
    ),
    # `Size / Dimension` is the length AND the width. Two keys claiming one
    # alias is legal exactly here, because each takes a different part of
    # the value rather than racing for the whole of it — see
    # `spec_extract.py` and the alias-uniqueness test.
    SpecKey(
        "length", "m", "Length", False,
        ("Size / Dimension",), ("Length",),
        extract="dimension_first",
    ),
    SpecKey(
        "width", "m", "Width", False,
        ("Size / Dimension",), ("Width",),
        extract="dimension_last",
    ),
    # A count, so no unit and no numeric sidecar — the same treatment
    # `hfe` and `unidirectional` already get.
    #
    # `Number of Terminations` is deliberately NOT an alias: it stays on
    # the junk denylist, where it was put because DigiKey files a two-pad
    # chip resistor's `2` under it. A passive does not need a pin count,
    # and reading one there would put it on several thousand of them.
    SpecKey(
        "pin_count", None, "Pin count", False,
        ("Number of Pins",), ("Number of Pins",),
    ),
    # Imperial-with-metric, like every other geometry DigiKey publishes.
    #
    # A connector overrides this with its own `pitch` (see
    # `COMMON_OVERRIDES`): the same upstream `Pitch` must not land on two
    # canonical keys of one part.
    SpecKey(
        "pin_pitch", "m", "Pin pitch", False,
        ("Pitch", "Lead Spacing", "Pin Pitch"),
        ("Pitch", "Pin Pitch"),
        extract="metric",
    ),
    # `Ratings` and `Qualification` were on the junk denylist, and most of
    # what they carry still is. The extractor keeps the AEC-Q token and
    # drops the prose, which is the whole reason they came off it.
    SpecKey(
        "automotive", None, "Automotive qualification", False,
        ("Ratings",), ("Qualification",),
        extract="aec_qualification",
    ),
    SpecKey(
        "device_marking", None, "Device marking", False,
        ("Part Marking", "Marking"), ("Marking",),
    ),
)


# ---------------------------------------------------------------------------
# The active-component classes.
#
# None of these had a canonical schema before: every value on an IC or a
# connector was kept verbatim under `optional`, so nothing sorted and
# nothing could be reported missing. Mandatory means "a part of this class
# without it is under-specified", not "the vendor always sends it".
# ---------------------------------------------------------------------------
_IC: tuple[SpecKey, ...] = (
    # DigiKey files the function under `Type` on most IC categories and
    # under `Function` on a few; Mouser calls it `Product`.
    SpecKey(
        "ic_type", None, "Function", True,
        ("Type", "Function"), ("Product", "Type"),
    ),
    SpecKey(
        "supply_voltage", "V", "Supply voltage", True,
        ("Voltage - Supply", "Voltage - Supply (Vcc/Vdd)"),
        ("Supply Voltage", "Operating Supply Voltage"),
    ),
    SpecKey(
        "channels", None, "Channels", False,
        ("Number of Channels",), ("Number of Channels",),
    ),
    SpecKey(
        "f_max", "Hz", "Maximum frequency", False,
        ("Speed", "Frequency", "Clock Frequency"),
        ("Maximum Frequency", "Speed"),
    ),
    SpecKey(
        "interface", None, "Interface", False,
        ("Interface",), ("Interface Type",),
    ),
)

_CONNECTOR: tuple[SpecKey, ...] = (
    SpecKey(
        "connector_type", None, "Connector type", True,
        ("Connector Type",), ("Product Type",),
    ),
    SpecKey(
        "positions", None, "Positions", True,
        ("Number of Positions",),
        ("Number of Positions", "Number of Contacts"),
    ),
    SpecKey("rows", None, "Rows", False, ("Number of Rows",), ("Number of Rows",)),
    # The class's own name for the common `pin_pitch`, which
    # `COMMON_OVERRIDES` removes here so one `Pitch` value writes one row.
    SpecKey("pitch", "m", "Pitch", False, ("Pitch",), ("Pitch",), extract="metric"),
    SpecKey("gender", None, "Gender", False, ("Gender",), ("Gender",)),
    SpecKey(
        "current_rating", "A", "Current rating", False,
        ("Current Rating (Amps)",), ("Current Rating",),
    ),
    SpecKey(
        "voltage_rating", "V", "Voltage rating", False,
        ("Voltage Rating",), ("Voltage Rating",),
    ),
    # `Mounting Type` is NOT an alias here: it is the common `mounting`,
    # and a through-hole right-angle header is both mounted and oriented.
    # Claiming it would put one value under two canonical keys.
    SpecKey("orientation", None, "Orientation", False, ("Orientation",), ("Orientation",)),
)

# One slug for crystals, oscillators and resonators, because the vendor
# category strings do not reliably separate them and the keys overlap.
# `load_capacitance` is mandatory on it: a crystal without one cannot be
# matched to a load, and an oscillator reads as incomplete rather than
# being filed somewhere the rest of its keys do not exist.
_CRYSTAL: tuple[SpecKey, ...] = (
    SpecKey("frequency", "Hz", "Frequency", True, ("Frequency",), ("Frequency",)),
    SpecKey(
        "load_capacitance", "F", "Load capacitance", True,
        ("Load Capacitance",), ("Load Capacitance",),
    ),
    SpecKey(
        "frequency_tolerance", "ppm", "Frequency tolerance", False,
        ("Frequency Tolerance",), ("Frequency Tolerance",),
    ),
    SpecKey(
        "frequency_stability", "ppm", "Frequency stability", False,
        ("Frequency Stability",), ("Frequency Stability",),
    ),
    SpecKey(
        "supply_voltage", "V", "Supply voltage", False,
        ("Voltage - Supply",), ("Supply Voltage",),
    ),
    SpecKey("crystal_type", None, "Type", False, ("Type",), ("Type",)),
)

_FUSE: tuple[SpecKey, ...] = (
    SpecKey(
        "current_rating", "A", "Current rating", True,
        ("Current Rating (Amps)",), ("Current Rating",),
    ),
    SpecKey(
        "voltage_rating", "V", "Voltage rating", True,
        ("Voltage Rating - AC", "Voltage Rating - DC"),
        ("Voltage Rating AC", "Voltage Rating DC"),
    ),
    SpecKey("fuse_type", None, "Fuse type", False, ("Fuse Type",), ("Fuse Type",)),
    SpecKey(
        "response_time", None, "Response time", False,
        ("Response Time",), ("Response Time",),
    ),
    # PTC resettables only; a one-shot fuse has neither.
    SpecKey(
        "hold_current", "A", "Hold current", False,
        ("Current - Hold (Ih) (Max)",), ("Hold Current",),
    ),
    SpecKey(
        "trip_current", "A", "Trip current", False,
        ("Current - Trip (It)",), ("Trip Current",),
    ),
)

_SWITCH: tuple[SpecKey, ...] = (
    # `Circuit` / `Contact Form` belong to `contact_config`, not here:
    # one upstream name cannot feed two canonical keys in one category,
    # and DigiKey sends both names on the same part.
    SpecKey(
        "switch_type", None, "Switch type", True,
        ("Switch Function",), ("Switch Type",),
    ),
    SpecKey(
        "contact_config", None, "Contact configuration", False,
        ("Circuit",), ("Contact Form",),
    ),
    # `2A @ 125VAC` — the leading term is the one the key asks about, the
    # same reading `0.063W, 1/16W` already gets.
    SpecKey(
        "current_rating", "A", "Current rating", False,
        ("Contact Rating @ Voltage",), ("Current Rating",),
    ),
    SpecKey("voltage_rating", "V", "Voltage rating", False, (), ("Voltage Rating",)),
    SpecKey(
        "operating_force", "N", "Operating force", False,
        ("Operating Force",), ("Actuation Force",),
    ),
    SpecKey(
        "electrical_life", "cycles", "Life", False,
        ("Mechanical Life",), ("Electrical Life",),
    ),
)

_TRANSFORMER: tuple[SpecKey, ...] = (
    SpecKey(
        "transformer_type", None, "Transformer type", True,
        ("Type",), ("Product Type",),
    ),
    # Volt-amperes, not watts: a transformer is rated in apparent power.
    # A value spelled in watts keeps its text and gets no sidecar rather
    # than being silently counted as VA.
    SpecKey(
        "power_rating", "VA", "Power rating", False,
        ("Power - Rated",), ("Power Rating",),
    ),
    SpecKey("turns_ratio", None, "Turns ratio", False, ("Turns Ratio",), ("Turns Ratio",)),
    SpecKey(
        "isolation_voltage", "V", "Isolation voltage", False,
        ("Isolation Voltage",), ("Isolation Voltage",),
    ),
    SpecKey(
        "primary_inductance", "H", "Primary inductance", False,
        ("Primary Inductance",), ("Primary Inductance",),
    ),
)

# A screw has no parametric spec set worth naming. `subtype` is what
# separates a standoff from a heatsink; the rest of what a mechanical part
# carries is geometry, which is on the common set.
_MECHANICAL: tuple[SpecKey, ...] = (
    SpecKey("subtype", None, "Type", False, ("Type",), ("Product Type",)),
)


# Category slug -> common keys that category answers with one of its own.
#
# `spec_keys_for` drops these from the common set for that slug. Without
# it a connector payload's `Pitch` would fill BOTH the common `pin_pitch`
# and the connector's `pitch` — two canonical rows for one fact, which is
# the thing ADR-0034 forbids most plainly.
COMMON_OVERRIDES: dict[str, frozenset[str]] = {
    "connector": frozenset({"pin_pitch"}),
}


# Category slug -> the keys that category adds on top of the common set.
MORE_CANONICAL_SPECS: dict[str, tuple[SpecKey, ...]] = {
    "ic": _IC,
    "connector": _CONNECTOR,
    "crystal": _CRYSTAL,
    "fuse": _FUSE,
    "switch": _SWITCH,
    "transformer": _TRANSFORMER,
    "mechanical": _MECHANICAL,
}
