"""The category-tree seed: which passive categories every workspace
should have, and what each one tells KiCad.

Pure data — no imports from the rest of the app, no DB, no I/O. The
logic that consumes it lives in `seed.py`, so "add a category" is a
one-row data edit. Mirrors the split between
`domain/parts/spec_schema_tables.py` and `spec_schema.py`.

Three separate contracts meet in every row, and all three are pinned by
`tests/test_category_seed.py`:

* **`value_template` reads canonical spec keys only.** The placeholders
  are resolved against the part's `custom_fields` rows by
  `domain/eda/value_template.py`, and the keys those rows carry are the
  ones `domain/parts/spec_schema_tables.py` defines for the category.
  A placeholder outside that set renders empty forever, so the test
  resolves each row's path through `spec_schema.category_slug_for` and
  refuses a key the resulting schema doesn't have.
* **`default_symbol_ref` names KiCad's own stock `Device` library.**
  Nothing is uploaded and nothing is packaged: `kicad_library.py`
  resolves external → hosted → category default, so one `Device:R`
  serves every resistor in the workspace instead of one vendor-zip
  symbol per part (ADR-0034 / plan B3).
* **Every row must validate as a `PartCategoryIn`.** The seed writes
  rows the API could have written — same slug shape, same placeholder
  grammar, same `kicad_fields` cap.

`library_slug` is workspace-unique rather than sibling-scoped (see
`models.py`), which is why a child's slug carries its parent:
`capacitors-ceramic`, not `ceramic`.

**Ambiguous roots carry no symbol.** A bare *Capacitors* could be a
ceramic or an electrolytic, and drawing a polarised part with a
non-polarised symbol is a schematic error, not a cosmetic one — so those
roots set `refdes_prefix` and footprint filters and leave
`default_symbol_ref` unset. `Diodes` and `Inductors` are not ambiguous
in that way: `Device:D` and `Device:L` are the generic members of their
own families.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SeedCategory:
    """One row the seed will create if the workspace hasn't got it.

    `parent` is another seed row's `name`, or ``None`` for a root. Rows
    are listed parent-first so one pass can resolve them in order.
    """

    name: str
    library_slug: str
    parent: str | None = None
    description: str | None = None
    sort_order: int = 0
    refdes_prefix: str | None = None
    default_symbol_ref: str | None = None
    footprint_filters: tuple[str, ...] | None = None
    value_template: str | None = None
    kicad_fields: tuple[str, ...] | None = None


# Field-by-field, what the seed is allowed to fill in on a category that
# already exists. `name`, `parent_id`, `library_slug` and `sort_order`
# are deliberately absent: the seed never renames, re-parents, or
# re-orders anything a user made.
SEEDABLE_FIELDS: tuple[str, ...] = (
    "description",
    "refdes_prefix",
    "default_symbol_ref",
    "footprint_filters",
    "value_template",
    "kicad_fields",
)

_RESISTOR_FIELDS = ("resistance", "tolerance", "power", "temp_coefficient", "package")
_CERAMIC_FIELDS = ("capacitance", "voltage_rating", "dielectric", "tolerance", "package")
_ELECTROLYTIC_FIELDS = (
    "capacitance",
    "voltage_rating",
    "esr",
    "ripple_current",
    "lifetime",
    "package",
)
_TANTALUM_FIELDS = ("capacitance", "voltage_rating", "tolerance", "esr", "package")
_FILM_FIELDS = ("capacitance", "voltage_rating", "tolerance", "dielectric", "package")
_INDUCTOR_FIELDS = (
    "inductance",
    "current_rating",
    "saturation_current",
    "dcr",
    "package",
)
_DIODE_FIELDS = ("diode_type", "vrrm", "if_avg", "vf", "package")
_ZENER_FIELDS = ("vz", "power", "package")
_TVS_FIELDS = (
    "v_reverse_standoff",
    "v_clamping",
    "power_peak_pulse",
    "unidirectional",
    "package",
)
_LED_FIELDS = ("color", "vf", "wavelength", "luminous_intensity", "package")
_BJT_FIELDS = ("transistor_type", "vceo", "ic_max", "power", "hfe", "package")
_MOSFET_FIELDS = ("fet_type", "vds", "id_max", "rds_on", "vgs_th", "package")

_INDUCTOR_TEMPLATE = "{inductance} {current_rating} {package}"
_DIODE_TEMPLATE = "{vrrm} {if_avg} {package}"
_BJT_TEMPLATE = "{vceo} {ic_max} {package}"
_MOSFET_TEMPLATE = "{vds} {id_max} {rds_on} {package}"

_TRANSISTOR_FOOTPRINTS = ("SOT*", "TO_*", "*SOT?23*")


SEED_CATEGORIES: tuple[SeedCategory, ...] = (
    # ---- Resistors: no children. The class has one spec set, and a
    # thick-film/thin-film split is a spec, not a category.
    SeedCategory(
        name="Resistors",
        library_slug="resistors",
        description="Fixed-value resistors.",
        sort_order=10,
        refdes_prefix="R",
        default_symbol_ref="Device:R",
        footprint_filters=("R_*",),
        value_template="{resistance} {tolerance} {package}",
        kicad_fields=_RESISTOR_FIELDS,
    ),
    # ---- Capacitors: dielectric decides both the spec set and whether
    # the symbol is polarised, so the root carries neither.
    SeedCategory(
        name="Capacitors",
        library_slug="capacitors",
        description="Capacitors, by dielectric.",
        sort_order=20,
        refdes_prefix="C",
        footprint_filters=("C_*", "CP_*"),
    ),
    SeedCategory(
        name="Ceramic",
        library_slug="capacitors-ceramic",
        parent="Capacitors",
        description="MLCC — C0G/NP0, X7R, X5R.",
        sort_order=10,
        refdes_prefix="C",
        default_symbol_ref="Device:C",
        footprint_filters=("C_*",),
        value_template="{capacitance} {voltage_rating} {dielectric} {package}",
        kicad_fields=_CERAMIC_FIELDS,
    ),
    SeedCategory(
        name="Electrolytic",
        library_slug="capacitors-electrolytic",
        parent="Capacitors",
        description="Aluminium electrolytic, polarised.",
        sort_order=20,
        refdes_prefix="C",
        default_symbol_ref="Device:C_Polarized",
        footprint_filters=("CP_*",),
        # No dielectric to print and no meaningful tolerance, so the
        # literal marks the class the way an engineer writes it on a
        # schematic: "1000 µF 50 V elyt".
        value_template="{capacitance} {voltage_rating} elyt",
        kicad_fields=_ELECTROLYTIC_FIELDS,
    ),
    SeedCategory(
        name="Tantalum",
        library_slug="capacitors-tantalum",
        parent="Capacitors",
        description="Tantalum, polarised.",
        sort_order=30,
        refdes_prefix="C",
        default_symbol_ref="Device:C_Polarized",
        footprint_filters=("CP_*",),
        value_template="{capacitance} {voltage_rating} tant",
        kicad_fields=_TANTALUM_FIELDS,
    ),
    SeedCategory(
        name="Film",
        library_slug="capacitors-film",
        parent="Capacitors",
        description="Polyester / polypropylene film.",
        sort_order=40,
        refdes_prefix="C",
        default_symbol_ref="Device:C",
        footprint_filters=("C_*",),
        value_template="{capacitance} {voltage_rating} {dielectric} {package}",
        kicad_fields=_FILM_FIELDS,
    ),
    # ---- Inductors.
    SeedCategory(
        name="Inductors",
        library_slug="inductors",
        description="Inductors, beads and chokes.",
        sort_order=30,
        refdes_prefix="L",
        default_symbol_ref="Device:L",
        footprint_filters=("L_*",),
        value_template=_INDUCTOR_TEMPLATE,
        kicad_fields=_INDUCTOR_FIELDS,
    ),
    SeedCategory(
        name="Power",
        library_slug="inductors-power",
        parent="Inductors",
        description="Power / energy-storage inductors.",
        sort_order=10,
        refdes_prefix="L",
        default_symbol_ref="Device:L",
        footprint_filters=("L_*",),
        value_template=_INDUCTOR_TEMPLATE,
        kicad_fields=_INDUCTOR_FIELDS,
    ),
    SeedCategory(
        name="Ferrite bead",
        library_slug="inductors-ferrite-bead",
        parent="Inductors",
        description="Ferrite beads — impedance at frequency, not inductance.",
        sort_order=20,
        refdes_prefix="FB",
        default_symbol_ref="Device:FerriteBead",
        footprint_filters=("L_*", "R_*"),
        value_template=_INDUCTOR_TEMPLATE,
        kicad_fields=_INDUCTOR_FIELDS,
    ),
    SeedCategory(
        name="Common-mode choke",
        library_slug="inductors-common-mode-choke",
        parent="Inductors",
        description="Two coupled windings on one core.",
        sort_order=30,
        refdes_prefix="L",
        default_symbol_ref="Device:L_Coupled",
        footprint_filters=("L_*",),
        value_template=_INDUCTOR_TEMPLATE,
        kicad_fields=_INDUCTOR_FIELDS,
    ),
    # ---- Diodes. `Device:D` is the generic member of the family, so
    # unlike Capacitors the root can carry it.
    SeedCategory(
        name="Diodes",
        library_slug="diodes",
        description="Diodes, by type.",
        sort_order=40,
        refdes_prefix="D",
        default_symbol_ref="Device:D",
        footprint_filters=("D_*",),
        value_template=_DIODE_TEMPLATE,
        kicad_fields=_DIODE_FIELDS,
    ),
    SeedCategory(
        name="Rectifier",
        library_slug="diodes-rectifier",
        parent="Diodes",
        description="Standard and fast-recovery rectifiers.",
        sort_order=10,
        refdes_prefix="D",
        default_symbol_ref="Device:D",
        footprint_filters=("D_*",),
        value_template=_DIODE_TEMPLATE,
        kicad_fields=_DIODE_FIELDS,
    ),
    SeedCategory(
        name="Schottky",
        library_slug="diodes-schottky",
        parent="Diodes",
        description="Schottky barrier diodes.",
        sort_order=20,
        refdes_prefix="D",
        default_symbol_ref="Device:D_Schottky",
        footprint_filters=("D_*",),
        value_template=_DIODE_TEMPLATE,
        kicad_fields=_DIODE_FIELDS,
    ),
    SeedCategory(
        name="Zener",
        library_slug="diodes-zener",
        parent="Diodes",
        description="Voltage-reference / clamping zeners.",
        sort_order=30,
        refdes_prefix="D",
        default_symbol_ref="Device:D_Zener",
        footprint_filters=("D_*",),
        value_template="{vz} {power} {package}",
        kicad_fields=_ZENER_FIELDS,
    ),
    SeedCategory(
        name="TVS",
        library_slug="diodes-tvs",
        parent="Diodes",
        description="Transient-voltage-suppression and ESD diodes.",
        sort_order=40,
        refdes_prefix="D",
        default_symbol_ref="Device:D_TVS",
        footprint_filters=("D_*",),
        value_template="{v_reverse_standoff} {power_peak_pulse} {package}",
        kicad_fields=_TVS_FIELDS,
    ),
    SeedCategory(
        name="LED",
        library_slug="diodes-led",
        parent="Diodes",
        description="Indicator and illumination LEDs.",
        sort_order=50,
        refdes_prefix="D",
        default_symbol_ref="Device:LED",
        footprint_filters=("LED*", "D_*"),
        value_template="{color} {package}",
        kicad_fields=_LED_FIELDS,
    ),
    # ---- Transistors. Ambiguous root, for the same reason as
    # Capacitors: NPN and P-channel are different symbols.
    SeedCategory(
        name="Transistors",
        library_slug="transistors",
        description="Discrete transistors, by type.",
        sort_order=50,
        refdes_prefix="Q",
        footprint_filters=_TRANSISTOR_FOOTPRINTS,
    ),
    SeedCategory(
        name="BJT NPN",
        library_slug="transistors-bjt-npn",
        parent="Transistors",
        description="NPN bipolar junction transistors.",
        sort_order=10,
        refdes_prefix="Q",
        default_symbol_ref="Device:Q_NPN_BCE",
        footprint_filters=_TRANSISTOR_FOOTPRINTS,
        value_template=_BJT_TEMPLATE,
        kicad_fields=_BJT_FIELDS,
    ),
    SeedCategory(
        name="BJT PNP",
        library_slug="transistors-bjt-pnp",
        parent="Transistors",
        description="PNP bipolar junction transistors.",
        sort_order=20,
        refdes_prefix="Q",
        default_symbol_ref="Device:Q_PNP_BCE",
        footprint_filters=_TRANSISTOR_FOOTPRINTS,
        value_template=_BJT_TEMPLATE,
        kicad_fields=_BJT_FIELDS,
    ),
    SeedCategory(
        name="MOSFET N",
        library_slug="transistors-mosfet-n",
        parent="Transistors",
        description="N-channel MOSFETs.",
        sort_order=30,
        refdes_prefix="Q",
        default_symbol_ref="Device:Q_NMOS_GDS",
        footprint_filters=_TRANSISTOR_FOOTPRINTS,
        value_template=_MOSFET_TEMPLATE,
        kicad_fields=_MOSFET_FIELDS,
    ),
    SeedCategory(
        name="MOSFET P",
        library_slug="transistors-mosfet-p",
        parent="Transistors",
        description="P-channel MOSFETs.",
        sort_order=40,
        refdes_prefix="Q",
        default_symbol_ref="Device:Q_PMOS_GDS",
        footprint_filters=_TRANSISTOR_FOOTPRINTS,
        value_template=_MOSFET_TEMPLATE,
        kicad_fields=_MOSFET_FIELDS,
    ),
)
