"""The spec-schema data: canonical keys per category, provider aliases,
the junk denylist and the catalog-vs-spec key list.

Pure data — no imports from the rest of the app, no DB, no I/O. The
logic that consumes it lives in `spec_schema.py`; splitting the two
keeps each file reviewable and makes "add a key" a one-line data edit.

Where the names come from:

* `digikey_aliases` are DigiKey `Parameters[].ParameterText` strings,
  verbatim. They are the only real parametric source we have.
* `mouser_aliases` cover both Mouser `ProductAttributes` names and the
  labels that `providers/mouser.py::parse_description_specs` invents
  when it mines the description (`Resistance`, `Power`, `Package`, …).
* Alias order is precedence order: the first alias present in a payload
  wins, and the rest are recorded as superseded.

`spec_schema_tables_more.py` holds the other half — the common optional
keys and the seven active-component classes, merged in below. Every rule
above applies to it unchanged.

See ADR-0034.
"""
from __future__ import annotations

import re

from app.domain.parts.spec_key import SpecKey
from app.domain.parts.spec_schema_tables_more import (
    EXTRA_COMMON_SPECS,
    MORE_CANONICAL_SPECS,
    MORE_CLASS_DEFAULT_SLUG,
    MORE_CLASS_RULES,
)

# ---------------------------------------------------------------------------
# Common keys — merged into every category, including unknown ones.
#
# The three below have been on every part since the schema shipped;
# `EXTRA_COMMON_SPECS` adds the optional geometry, pin and qualification
# keys.
# ---------------------------------------------------------------------------
_BASE_COMMON_SPECS: tuple[SpecKey, ...] = (
    SpecKey(
        "package", None, "Package", True,
        ("Package / Case", "Supplier Device Package", "Package"),
        ("Package", "Package / Case", "Case", "Case Code"),
    ),
    SpecKey(
        "mounting", None, "Mounting", False,
        ("Mounting Type",),
        ("Mounting Style", "Mounting Type"),
    ),
    SpecKey(
        "operating_temp", "°C", "Operating temperature", False,
        ("Operating Temperature", "Operating Temperature - Junction"),
        ("Operating Temperature", "Operating Temperature Range"),
    ),
)

COMMON_SPECS: tuple[SpecKey, ...] = _BASE_COMMON_SPECS + EXTRA_COMMON_SPECS

_RESISTOR: tuple[SpecKey, ...] = (
    SpecKey("resistance", "Ω", "Resistance", True, ("Resistance",), ("Resistance",)),
    SpecKey("tolerance", "%", "Tolerance", True, ("Tolerance",), ("Tolerance",)),
    SpecKey("power", "W", "Power", True, ("Power (Watts)",), ("Power", "Power Rating")),
    SpecKey(
        "temp_coefficient", "ppm/°C", "Temperature coefficient", True,
        ("Temperature Coefficient",), ("Temperature Coefficient",),
    ),
)

_ESR_DIGIKEY = ("ESR (Equivalent Series Resistance)",)

_CAPACITANCE = SpecKey(
    "capacitance", "F", "Capacitance", True, ("Capacitance",), ("Capacitance",)
)
_CAP_VOLTAGE = SpecKey(
    "voltage_rating", "V", "Voltage rating", True,
    ("Voltage - Rated",), ("Voltage Rating", "Voltage Rating DC", "Voltage"),
)
_CAP_TOLERANCE = SpecKey(
    "tolerance", "%", "Tolerance", True, ("Tolerance",), ("Tolerance",)
)

_CAPACITOR_CERAMIC: tuple[SpecKey, ...] = (
    _CAPACITANCE,
    _CAP_VOLTAGE,
    # DigiKey files X7R/C0G under "Temperature Coefficient" for ceramics —
    # the same ParameterText a resistor uses for its ppm/°C figure. The two
    # never collide because the alias is only consulted for the category
    # the part is actually in.
    SpecKey(
        "dielectric", None, "Dielectric", True,
        ("Temperature Coefficient",), ("Dielectric", "Dielectric Material"),
    ),
    _CAP_TOLERANCE,
)

_CAPACITOR_ELECTROLYTIC: tuple[SpecKey, ...] = (
    _CAPACITANCE,
    _CAP_VOLTAGE,
    SpecKey("esr", "Ω", "ESR", True, _ESR_DIGIKEY, ("ESR",)),
    SpecKey(
        "ripple_current", "A", "Ripple current", True,
        ("Ripple Current @ Low Frequency", "Ripple Current @ High Frequency"),
        ("Ripple Current",),
    ),
    SpecKey("lifetime", None, "Lifetime", True, ("Lifetime @ Temp.",), ("Lifetime",)),
)

_CAPACITOR_TANTALUM: tuple[SpecKey, ...] = (
    _CAPACITANCE,
    _CAP_VOLTAGE,
    _CAP_TOLERANCE,
    SpecKey("esr", "Ω", "ESR", False, _ESR_DIGIKEY, ("ESR",)),
)

_CAPACITOR_FILM: tuple[SpecKey, ...] = (
    _CAPACITANCE,
    _CAP_VOLTAGE,
    _CAP_TOLERANCE,
    SpecKey(
        "dielectric", None, "Dielectric", False,
        ("Dielectric Material",), ("Dielectric Material", "Dielectric"),
    ),
)

_INDUCTOR: tuple[SpecKey, ...] = (
    SpecKey("inductance", "H", "Inductance", True, ("Inductance",), ("Inductance",)),
    SpecKey(
        "current_rating", "A", "Current rating", True,
        ("Current Rating (Amps)",), ("Current Rating", "Current"),
    ),
    SpecKey(
        "saturation_current", "A", "Saturation current", True,
        ("Current - Saturation",), ("Saturation Current",),
    ),
    SpecKey(
        "dcr", "Ω", "DC resistance", True,
        ("DC Resistance (DCR)",), ("DC Resistance", "DCR"),
    ),
)

_DIODE: tuple[SpecKey, ...] = (
    SpecKey(
        "diode_type", None, "Diode type", True,
        ("Diode Type",), ("Diode Type", "Configuration"),
    ),
    SpecKey(
        "vrrm", "V", "Reverse voltage", True,
        ("Voltage - DC Reverse (Vr) (Max)",), ("Reverse Voltage", "Voltage"),
    ),
    SpecKey(
        "if_avg", "A", "Forward current", True,
        ("Current - Average Rectified (Io)",), ("Forward Current", "Current"),
    ),
    SpecKey(
        "vf", "V", "Forward voltage", True,
        ("Voltage - Forward (Vf) (Max) @ If",), ("Forward Voltage",)
    ),
)

_DIODE_ZENER: tuple[SpecKey, ...] = (
    SpecKey(
        "vz", "V", "Zener voltage", True,
        ("Voltage - Zener (Nom) (Vz)",), ("Zener Voltage", "Voltage"),
    ),
    SpecKey("power", "W", "Power", True, ("Power - Max",), ("Power", "Power Dissipation")),
)

_DIODE_TVS: tuple[SpecKey, ...] = (
    SpecKey(
        "v_reverse_standoff", "V", "Reverse standoff voltage", True,
        ("Voltage - Reverse Standoff (Typ)",), ("Reverse Standoff Voltage",),
    ),
    SpecKey(
        "v_clamping", "V", "Clamping voltage", True,
        ("Voltage - Clamping (Max) @ Ipp",), ("Clamping Voltage",),
    ),
    SpecKey(
        "power_peak_pulse", "W", "Peak pulse power", True,
        ("Power - Peak Pulse",), ("Peak Pulse Power",),
    ),
    SpecKey(
        "unidirectional", None, "Unidirectional", True,
        ("Unidirectional Channels",), ("Polarity",),
    ),
)

_LED: tuple[SpecKey, ...] = (
    SpecKey("color", None, "Colour", True, ("Color",), ("Color", "LED Color")),
    SpecKey(
        "vf", "V", "Forward voltage", True,
        ("Voltage - Forward (Vf) (Typ)",), ("Forward Voltage",),
    ),
    # Base unit metre, so `625nm` sorts against `470nm`; display stays nm.
    SpecKey(
        "wavelength", "m", "Wavelength", True,
        ("Wavelength - Dominant", "Wavelength - Peak"), ("Wavelength",),
    ),
    SpecKey(
        "luminous_intensity", "cd", "Luminous intensity", True,
        ("Millicandela Rating",), ("Luminous Intensity",),
    ),
)

_TRANSISTOR_BJT: tuple[SpecKey, ...] = (
    SpecKey(
        "transistor_type", None, "Transistor type", True,
        ("Transistor Type",), ("Transistor Type", "Polarity"),
    ),
    SpecKey(
        "vceo", "V", "Vceo", True,
        ("Voltage - Collector Emitter Breakdown (Max)",),
        ("Collector-Emitter Voltage",),
    ),
    SpecKey(
        "ic_max", "A", "Collector current", True,
        ("Current - Collector (Ic) (Max)",),
        ("Collector Current", "Continuous Collector Current"),
    ),
    SpecKey("power", "W", "Power", True, ("Power - Max",), ("Power Dissipation", "Power")),
    SpecKey(
        "hfe", None, "hFE", True,
        ("DC Current Gain (hFE) @ Ic, Vce",), ("DC Current Gain hFE", "Current Gain"),
    ),
)

_TRANSISTOR_MOSFET: tuple[SpecKey, ...] = (
    SpecKey(
        "fet_type", None, "FET type", True,
        ("FET Type",), ("Transistor Polarity", "FET Type"),
    ),
    SpecKey(
        "vds", "V", "Vds", True,
        ("Drain to Source Voltage (Vdss)",), ("Drain-Source Voltage",),
    ),
    SpecKey(
        "id_max", "A", "Drain current", True,
        ("Current - Continuous Drain (Id) @ 25°C",),
        ("Drain Current", "Continuous Drain Current"),
    ),
    SpecKey(
        "rds_on", "Ω", "Rds(on)", True,
        ("Rds On (Max) @ Id, Vgs",), ("Rds On Resistance", "Drain-Source Resistance"),
    ),
    SpecKey(
        "vgs_th", "V", "Vgs(th)", True,
        ("Vgs(th) (Max) @ Id",), ("Gate Threshold Voltage",),
    ),
)

# Category slug -> the keys that category adds on top of COMMON_SPECS.
CANONICAL_SPECS: dict[str, tuple[SpecKey, ...]] = {
    "common": COMMON_SPECS,
    "resistor": _RESISTOR,
    "capacitor_ceramic": _CAPACITOR_CERAMIC,
    "capacitor_electrolytic": _CAPACITOR_ELECTROLYTIC,
    "capacitor_tantalum": _CAPACITOR_TANTALUM,
    "capacitor_film": _CAPACITOR_FILM,
    "inductor": _INDUCTOR,
    "diode": _DIODE,
    "diode_schottky": _DIODE,
    "diode_zener": _DIODE_ZENER,
    "diode_tvs": _DIODE_TVS,
    "led": _LED,
    "transistor_bjt": _TRANSISTOR_BJT,
    "transistor_mosfet": _TRANSISTOR_MOSFET,
    **MORE_CANONICAL_SPECS,
}


# ---------------------------------------------------------------------------
# Junk denylist — compliance codes and packaging trivia that are not specs.
# Measured on prod 2026-09-15: HTS code 246 rows, ECCN 277, MSL 246, plus
# eight per-country HTS variants and ~1,000 rows whose value is "-".
#
# `Ratings` (DigiKey) and `Qualification` (Mouser) were on this list and
# are not any more: they feed the common `automotive` key, whose extractor
# keeps the AEC-Q token and drops everything else under them. Taking a key
# off this list is only safe when something downstream refuses the prose —
# here, `spec_extract.aec_qualification` returning ``None``.
# ---------------------------------------------------------------------------
DROP_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^(?:TARIC|CNHTS|BRHTS|USHTS|JPHTS|KRHTS|MXHTS|CAHTS|HTS code|ECCN"
        r"|Conflict Minerals|MSL|NCNR|IPC code|Base Product Number"
        r"|Number of Terminations|Restriction"
        r"|Suggested replacement|Unit weight)$",
        re.IGNORECASE,
    ),
    # Every other country's HTS column, so a new one needs no code change.
    re.compile(r"^[A-Z]{2}HTS$"),
)

JUNK_VALUES: frozenset[str] = frozenset({"", "-", "–", "—", "n/a", "na", "none", "null"})


# ---------------------------------------------------------------------------
# Catalog metadata — real data, but availability/pricing/packaging, not a
# parametric spec. Mirrored by `CATALOG_LITERAL_KEYS` in
# `web/src/lib/providerCatalog.ts`; `tests/test_spec_schema.py` fails if the
# two drift apart.
# ---------------------------------------------------------------------------
CATALOG_LITERAL_KEYS: frozenset[str] = frozenset({
    "Alternate packagings",
    "Availability",
    "Backorder allowed",
    "Detailed description",
    "DigiKey P/N",
    "Discontinued",
    "End of life",
    "In stock (qty)",
    "LCSC",
    "Lead time",
    "Lifecycle",
    "Marketplace",
    "MOQ",
    "Max order qty",
    "Mouser P/N",
    "On order (qty)",
    "Order multiple",
    "Packaging",
    "REACH",
    "RoHS",
    "Series",
    "Standard Pack Qty",
})

CATALOG_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^Unit price \(\d+\+\)$"),
)


# ---------------------------------------------------------------------------
# Our category names -> schema slugs. Provider-category -> our-category is a
# different mapping and lives in A4, not here.
# ---------------------------------------------------------------------------
# Two passes, and the order matters. The *class* is decided first, from the
# component noun; only then is the class refined by a modifier word. Doing it
# in one flat first-match-wins pass is what made "Thick Film Resistors" a film
# capacitor and "Ceramic Resonators" a ceramic capacitor — "film" and
# "ceramic" are not component nouns, they are adjectives that mean different
# things under different classes.
#
# `led` is checked before `diode` on purpose: "Diodes / LED" contains both
# nouns and is an LED.
CLASS_RULES: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"led", "leds"}), "led"),
    (frozenset({"resistor", "resistors"}), "resistor"),
    (frozenset({"capacitor", "capacitors"}), "capacitor"),
    (frozenset({"inductor", "inductors", "choke", "chokes"}), "inductor"),
    (frozenset({"transistor", "transistors", "mosfet", "mosfets", "bjt"}), "transistor"),
    (
        frozenset({"diode", "diodes", "rectifier", "rectifiers", "zener", "schottky", "tvs"}),
        "diode",
    ),
    # ICs, connectors, crystals, fuses, switches, transformers, mechanical —
    # in `spec_schema_tables_more.py`, next to their spec keys, with the
    # three taxonomy overlaps their order decides written out there.
    *MORE_CLASS_RULES,
)

# class -> ((modifier words, slug), …). First match wins within a class.
REFINEMENT_RULES: dict[str, tuple[tuple[frozenset[str], str], ...]] = {
    "capacitor": (
        (frozenset({"ceramic"}), "capacitor_ceramic"),
        (frozenset({"electrolytic", "aluminum", "aluminium"}), "capacitor_electrolytic"),
        (frozenset({"tantalum"}), "capacitor_tantalum"),
        (frozenset({"film", "polyester", "polypropylene"}), "capacitor_film"),
    ),
    "transistor": (
        (frozenset({"mosfet", "mosfets", "fet", "fets"}), "transistor_mosfet"),
        (frozenset({"bjt", "npn", "pnp", "bipolar"}), "transistor_bjt"),
    ),
    "diode": (
        (frozenset({"zener"}), "diode_zener"),
        (frozenset({"schottky"}), "diode_schottky"),
        (frozenset({"tvs", "transient", "esd"}), "diode_tvs"),
        (frozenset({"led", "leds"}), "led"),
    ),
}

# What a class resolves to when no modifier matches. A bare "Capacitors" or
# "Transistors" is genuinely ambiguous — the dielectric and the channel type
# change the whole spec set — so it stays ``None`` and the part keeps only
# the common keys.
CLASS_DEFAULT_SLUG: dict[str, str | None] = {
    "led": "led",
    "resistor": "resistor",
    "inductor": "inductor",
    "diode": "diode",
    "capacitor": None,
    "transistor": None,
    **MORE_CLASS_DEFAULT_SLUG,
}

PATH_SEPARATOR_RE = re.compile(r"[/>|»]")
WORD_RE = re.compile(r"[a-z]+")
