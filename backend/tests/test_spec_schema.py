"""`domain/parts/spec_schema.py` — the per-category canonical spec schema.

Pins the four things that make the schema safe to wire into import and
refresh later (A3): the tables are internally consistent, the junk
denylist actually catches the keys measured on prod, a real provider
payload maps to the canonical keys we expect, and the backend catalog
list does not drift away from the frontend one.
"""
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.parts.spec_schema import (
    CANONICAL_SPECS,
    CATALOG_LITERAL_KEYS,
    category_slug_for,
    is_catalog_key,
    is_junk_key,
    is_junk_value,
    missing_mandatory,
    normalise,
    spec_keys_for,
)
from app.domain.parts.spec_schema_tables import DROP_KEY_PATTERNS

_PROVIDER_CATALOG_TS = (
    Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "providerCatalog.ts"
)


# ---------------------------------------------------------------------------
# Table consistency
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("slug", sorted(CANONICAL_SPECS))
def test_every_category_has_at_least_one_mandatory_key(slug: str) -> None:
    assert [s for s in spec_keys_for(slug) if s.mandatory], slug


@pytest.mark.parametrize("slug", sorted(CANONICAL_SPECS))
@pytest.mark.parametrize("provider", ["digikey", "mouser"])
def test_aliases_are_unique_within_a_category(slug: str, provider: str) -> None:
    # Arrange — two canonical keys claiming the same upstream name would
    # make the winner depend on tuple order, i.e. on nothing.
    owner_of: dict[str, str] = {}

    shared_ok: dict[str, bool] = {}

    # Act / Assert
    for spec in spec_keys_for(slug):
        aliases = spec.mouser_aliases if provider == "mouser" else spec.digikey_aliases
        for alias in aliases:
            # The one legal exception: two keys may share an alias when BOTH
            # declare an extractor, because then each takes a different part
            # of one value (`Size / Dimension` is a length AND a width) and
            # the outcome does not depend on tuple order. See ADR-0034.
            if alias in owner_of:
                assert shared_ok.get(alias) and spec.extract, (
                    f"{slug}/{provider}: '{alias}' claimed by both "
                    f"'{owner_of.get(alias)}' and '{spec.key}'"
                )
            owner_of[alias] = spec.key
            shared_ok[alias] = bool(spec.extract)


@pytest.mark.parametrize("slug", sorted(CANONICAL_SPECS))
def test_canonical_keys_are_unique_within_a_category(slug: str) -> None:
    keys = [s.key for s in spec_keys_for(slug)]
    assert len(keys) == len(set(keys)), slug


@pytest.mark.parametrize("slug", sorted(CANONICAL_SPECS))
def test_every_category_carries_the_common_keys(slug: str) -> None:
    assert {"package", "mounting", "operating_temp"} <= {
        s.key for s in spec_keys_for(slug)
    }


COMMON_KEYS = [
    "package",
    "mounting",
    "operating_temp",
    "height",
    "length",
    "width",
    "pin_count",
    "pin_pitch",
    "automotive",
    "device_marking",
]


def test_unknown_category_falls_back_to_the_common_keys() -> None:
    assert [s.key for s in spec_keys_for("not_a_category")] == COMMON_KEYS
    assert [s.key for s in spec_keys_for(None)] == COMMON_KEYS


# ---------------------------------------------------------------------------
# Junk denylist
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "key",
    [
        "TARIC", "CNHTS", "BRHTS", "USHTS", "JPHTS", "KRHTS", "MXHTS", "CAHTS",
        "HTS code", "ECCN", "MSL", "Unit weight", "NCNR", "IPC code",
        "Conflict Minerals", "Base Product Number", "Number of Terminations",
        "Restriction", "Suggested replacement",
    ],
)
def test_is_junk_key_catches_the_keys_measured_on_prod(key: str) -> None:
    assert is_junk_key(key) is True


@pytest.mark.parametrize(
    "key",
    ["Resistance", "Capacitance", "Package / Case", "Composition", "Features", "Series"],
)
def test_is_junk_key_leaves_real_keys_alone(key: str) -> None:
    assert is_junk_key(key) is False


@pytest.mark.parametrize("value", ["-", "", "   ", "N/A", "none"])
def test_is_junk_value_rejects_placeholders(value: str) -> None:
    assert is_junk_value(value) is True


@pytest.mark.parametrize("value", ["0", "0 Ohms", "-55°C ~ 125°C", "X7R"])
def test_is_junk_value_keeps_real_values(value: str) -> None:
    assert is_junk_value(value) is False


def test_drop_patterns_are_anchored_at_both_ends() -> None:
    # Arrange / Act / Assert — "ECCN notes" is a different field.
    assert not any(p.match("ECCN notes") for p in DROP_KEY_PATTERNS)
    assert not any(p.match("Pre-TARIC") for p in DROP_KEY_PATTERNS)


# ---------------------------------------------------------------------------
# normalise() — DigiKey
# ---------------------------------------------------------------------------
DIGIKEY_RESISTOR: list[tuple[str, str]] = [
    ("Resistance", "10 kOhms"),
    ("Tolerance", "±1%"),
    ("Power (Watts)", "0.063W, 1/16W"),
    ("Temperature Coefficient", "±100ppm/°C"),
    ("Package / Case", "0402 (1005 Metric)"),
    ("Supplier Device Package", "0402"),
    ("Mounting Type", "Surface Mount"),
    ("Operating Temperature", "-55°C ~ 155°C"),
    ("Composition", "Thick Film"),
    ("Features", "-"),
    ("Number of Terminations", "2"),
    ("TARIC", "8533210000"),
    ("ECCN", "EAR99"),
    ("MSL", "1 (Unlimited)"),
    ("Lifecycle", "Active"),
    ("RoHS", "ROHS3 Compliant"),
    ("In stock (qty)", "12500"),
    ("Unit price (1+)", "0.10"),
    ("DigiKey P/N", "311-10.0KLRCT-ND"),
]


def test_digikey_resistor_payload_maps_to_the_canonical_keys() -> None:
    # Act
    result = normalise("resistor", "digikey", DIGIKEY_RESISTOR)

    # Assert — canonical display strings and their numeric sidecars.
    assert {k: v.display for k, v in result.canonical.items()} == {
        "package": "0402 (1005 Metric)",
        "mounting": "Surface Mount",
        "operating_temp": "-55°C ~ 155°C",
        "resistance": "10 kΩ",
        "tolerance": "1%",
        "power": "63 mW",
        "temp_coefficient": "100 ppm/°C",
    }
    assert result.canonical["resistance"].value_num == Decimal("10000")
    assert result.canonical["resistance"].unit == "Ω"
    assert result.canonical["power"].value_num == Decimal("0.063")
    # A range has a display but no single number to sort on.
    assert result.canonical["operating_temp"].value_num is None


def test_digikey_resistor_payload_routes_catalog_junk_and_optional() -> None:
    result = normalise("resistor", "digikey", DIGIKEY_RESISTOR)

    assert result.catalog == {
        "Lifecycle": "Active",
        "RoHS": "ROHS3 Compliant",
        "In stock (qty)": "12500",
        "Unit price (1+)": "0.10",
        "DigiKey P/N": "311-10.0KLRCT-ND",
    }
    # Everything genuinely parametric that has no canonical slot survives.
    assert result.optional == {"Composition": "Thick Film"}
    # `Features` is dropped for its `-` value, the rest for their key.
    assert set(result.dropped) == {
        "Features",
        "Number of Terminations",
        "TARIC",
        "ECCN",
        "MSL",
        "Supplier Device Package",
    }


def test_the_first_listed_alias_wins_and_the_loser_is_recorded() -> None:
    # Arrange — both DigiKey package fields are present.
    result = normalise("resistor", "digikey", DIGIKEY_RESISTOR)

    # Assert — `Package / Case` is first in the alias tuple, so it wins,
    # and the duplicate never reaches the Specs tab as a second row.
    assert result.canonical["package"].raw_key == "Package / Case"
    assert "Supplier Device Package" in result.dropped
    assert "Supplier Device Package" not in result.optional


def test_dash_valued_rows_never_reach_canonical_or_optional() -> None:
    result = normalise("resistor", "digikey", [("Resistance", "-"), ("Features", "-")])

    assert result.canonical == {}
    assert result.optional == {}
    assert sorted(result.dropped) == ["Features", "Resistance"]


def test_a_ceramic_capacitor_reads_its_dielectric_from_temperature_coefficient() -> None:
    # Arrange — DigiKey files X7R under the same ParameterText a resistor
    # uses for ppm/°C. The category decides which key it means.
    payload = [
        ("Capacitance", "100nF"),
        ("Voltage - Rated", "50V"),
        ("Temperature Coefficient", "X7R"),
        ("Tolerance", "±10%"),
        ("Package / Case", "0603 (1608 Metric)"),
    ]

    # Act
    result = normalise("capacitor_ceramic", "digikey", payload)

    # Assert
    assert result.canonical["dielectric"].display == "X7R"
    assert result.canonical["capacitance"].display == "100 nF"
    assert result.canonical["capacitance"].value_num == Decimal("0.0000001")
    assert result.canonical["voltage_rating"].display == "50 V"
    assert missing_mandatory("capacitor_ceramic", result.canonical) == []


def test_an_unparseable_value_under_a_unit_key_keeps_its_text() -> None:
    # Arrange / Act — never invent a number for text we cannot read.
    result = normalise("resistor", "digikey", [("Resistance", "See datasheet")])

    # Assert
    spec = result.canonical["resistance"]
    assert spec.display == "See datasheet"
    assert spec.value_num is None
    assert spec.unit == "Ω"


# ---------------------------------------------------------------------------
# normalise() — Mouser
# ---------------------------------------------------------------------------
MOUSER_DESCRIPTION = (
    "Thick Film Resistors - SMD General Purpose Chip Resistor "
    "0402, 10kOhms, 1%, 1/16W"
)


def test_mouser_resistor_falls_back_to_the_description_regexes() -> None:
    # Arrange — Mouser's ProductAttributes is packaging, nothing else.
    attributes = [("Packaging", "Cut Tape"), ("Standard Pack Qty", "5000")]

    # Act
    result = normalise(
        "resistor", "mouser", attributes, description=MOUSER_DESCRIPTION
    )

    # Assert — the parametric values come out of the description text.
    assert {k: v.display for k, v in result.canonical.items()} == {
        "package": "0402",
        "resistance": "10 kΩ",
        "tolerance": "1%",
        "power": "62.5 mW",
    }
    assert result.catalog == {"Packaging": "Cut Tape", "Standard Pack Qty": "5000"}
    # Mouser has no temperature coefficient anywhere, and that is the point
    # of the flag A3 will surface.
    assert missing_mandatory("resistor", result.canonical) == ["temp_coefficient"]


def test_a_mouser_attribute_beats_the_description_for_the_same_key() -> None:
    # Arrange — the attribute is authoritative; the description is mined.
    attributes = [("Resistance", "4.7 kOhms")]

    # Act
    result = normalise(
        "resistor", "mouser", attributes, description=MOUSER_DESCRIPTION
    )

    # Assert
    assert result.canonical["resistance"].display == "4.7 kΩ"


def test_the_description_is_ignored_for_digikey() -> None:
    result = normalise("resistor", "digikey", [], description=MOUSER_DESCRIPTION)

    assert result.canonical == {}


def test_reserved_provider_keys_never_land_in_the_spec_buckets() -> None:
    # Arrange — image_url/datasheet_url/source_url are provider metadata
    # and belong in neither tab.
    result = normalise(
        "resistor", "digikey", [("image_url", "https://example.invalid/i.jpg")]
    )

    # Assert
    assert result.optional == {}
    assert result.canonical == {}
    assert "image_url" in result.catalog


def test_normalise_is_total_on_an_empty_payload() -> None:
    result = normalise(None, "digikey", [])

    assert (result.canonical, result.optional, result.catalog, result.dropped) == (
        {}, {}, {}, [],
    )


# ---------------------------------------------------------------------------
# missing_mandatory / category_slug_for
# ---------------------------------------------------------------------------
def test_missing_mandatory_lists_unfilled_keys_in_schema_order() -> None:
    assert missing_mandatory("resistor", ["resistance", "package"]) == [
        "tolerance",
        "power",
        "temp_coefficient",
    ]


@pytest.mark.parametrize(
    ("name_path", "slug"),
    [
        ("Resistors", "resistor"),
        ("Capacitors / Ceramic", "capacitor_ceramic"),
        ("Capacitors / Electrolytic", "capacitor_electrolytic"),
        ("Capacitors / Aluminum Electrolytic", "capacitor_electrolytic"),
        ("Capacitors / Tantalum", "capacitor_tantalum"),
        ("Capacitors / Film", "capacitor_film"),
        ("Inductors", "inductor"),
        ("Inductors / Ferrite bead", "inductor"),
        ("Inductors / Common-mode choke", "inductor"),
        ("Diodes", "diode"),
        ("Diodes / Rectifier", "diode"),
        ("Diodes / Schottky", "diode_schottky"),
        ("Diodes / Zener", "diode_zener"),
        ("Diodes / TVS", "diode_tvs"),
        ("Diodes / LED", "led"),
        ("Transistors / MOSFET N", "transistor_mosfet"),
        ("Transistors / MOSFET P", "transistor_mosfet"),
        ("Transistors / BJT NPN", "transistor_bjt"),
        ("Transistors / BJT PNP", "transistor_bjt"),
        # Separator-agnostic, and the leaf name alone is enough.
        ("Capacitors > Ceramic", "capacitor_ceramic"),
        ("Zener diodes", "diode_zener"),
    ],
)
def test_category_slug_for_maps_our_names(name_path: str, slug: str) -> None:
    assert category_slug_for(name_path) == slug


@pytest.mark.parametrize(
    "name_path",
    [
        None,
        "",
        "Connectors",
        "Microcontrollers",
        # Ambiguous on purpose: the dielectric / channel type changes the
        # whole spec set, so a bare root must not pick one.
        "Capacitors",
        "Transistors",
    ],
)
def test_category_slug_for_refuses_what_it_cannot_place(name_path: str | None) -> None:
    assert category_slug_for(name_path) is None


# ---------------------------------------------------------------------------
# Frontend / backend catalog-list agreement
#
# The split is rendered by `web/src/lib/providerCatalog.ts` and decided by
# this module; a key present in one list and absent from the other means a
# row shows up on the wrong tab. Reading the TS source is the cheapest
# check that does not need a build step or a generated artifact.
# ---------------------------------------------------------------------------
def _frontend_catalog_literals() -> set[str]:
    source = _PROVIDER_CATALOG_TS.read_text(encoding="utf-8")
    block = re.search(
        r"const CATALOG_LITERAL_KEYS = new Set<string>\(\[(.*?)\]\)", source, re.DOTALL
    )
    assert block is not None, "CATALOG_LITERAL_KEYS not found in providerCatalog.ts"
    return set(re.findall(r'"([^"]+)"', block.group(1)))


def test_every_backend_catalog_key_is_hidden_from_the_frontend_specs_tab() -> None:
    assert CATALOG_LITERAL_KEYS <= _frontend_catalog_literals()


def test_every_frontend_catalog_key_is_catalog_or_junk_on_the_backend() -> None:
    # `HTS code` and `ECCN` are on both the frontend catalog list and the
    # backend denylist: the frontend keeps legacy rows off the Specs tab,
    # the backend stops writing them at all.
    for key in _frontend_catalog_literals():
        assert is_catalog_key(key) or is_junk_key(key), key


def test_the_priced_row_regex_is_spelled_the_same_on_both_sides() -> None:
    source = _PROVIDER_CATALOG_TS.read_text(encoding="utf-8")

    assert r"/^Unit price \(\d+\+\)$/" in source
    assert is_catalog_key("Unit price (100+)") is True
    assert is_catalog_key("Unit price") is False


# ---------------------------------------------------------------------------
# Regressions from review
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name_path", "slug"),
    [
        # `film` and `ceramic` are adjectives, not component nouns. A flat
        # first-match-wins pass made every thick-film resistor a film
        # capacitor and every ceramic resonator a ceramic capacitor.
        ("Thick Film Resistors - SMD", "resistor"),
        ("Resistors / Metal Film", "resistor"),
        ("Chip Resistor - Surface Mount / Thin Film", "resistor"),
        ("Aluminum Electrolytic Capacitors", "capacitor_electrolytic"),
    ],
)
def test_an_adjective_never_outvotes_the_component_noun(
    name_path: str, slug: str
) -> None:
    assert category_slug_for(name_path) == slug


def test_ceramic_without_a_component_noun_is_not_a_capacitor() -> None:
    assert category_slug_for("Ceramic Resonators") is None


def test_a_real_mouser_attribute_beats_a_description_derived_key() -> None:
    # Arrange — the mined labels ("Package", "Power") sit EARLIER in their
    # alias tuples than the real attribute names ("Case Code", "Power
    # Rating"), so payload order alone does not protect the attribute.
    attributes = [("Case Code", "0805"), ("Power Rating", "0.25W")]

    # Act
    result = normalise(
        "resistor", "mouser", attributes, description=MOUSER_DESCRIPTION
    )

    # Assert — the attribute wins for both keys, and the mined row is the
    # one recorded as superseded.
    assert result.canonical["package"].raw_key == "Case Code"
    assert result.canonical["package"].display == "0805"
    assert result.canonical["power"].raw_key == "Power Rating"
    assert result.canonical["power"].display == "250 mW"
    assert "Package" in result.dropped
    assert "Power" in result.dropped


def test_a_key_repeated_with_a_placeholder_keeps_its_real_value() -> None:
    # Arrange — Mouser repeats AttributeName, and `-` is ~1,000 prod rows.
    payload = [("Tolerance", "-"), ("Tolerance", "±1%")]

    # Act
    result = normalise("resistor", "digikey", payload)

    # Assert — one bucket, not two.
    assert result.canonical["tolerance"].display == "1%"
    assert result.dropped == []


@pytest.mark.parametrize(
    "payload",
    [
        [("Tolerance", "-"), ("Tolerance", "±1%")],
        [("Voltage - Rated", "-"), ("Voltage - Rated", "-")],
        [("Resistance", "10 kOhms"), ("Resistance", "4.7 kOhms")],
        [("", "orphan"), ("Composition", "Thick Film")],
        DIGIKEY_RESISTOR,
    ],
)
def test_the_four_buckets_partition_the_payload(payload) -> None:
    # Arrange / Act
    result = normalise("resistor", "digikey", payload)

    # Assert — no key in two buckets, and none listed twice.
    canonical_raw = [spec.raw_key for spec in result.canonical.values()]
    buckets = canonical_raw + list(result.optional) + list(result.catalog) + result.dropped
    assert len(buckets) == len(set(buckets)), buckets
    # And nothing non-blank is lost.
    supplied = {key.strip() for key, _ in payload if key.strip()}
    assert supplied == set(buckets)


def test_a_value_in_the_wrong_unit_gets_no_numeric_sidecar() -> None:
    # Arrange / Act — a mis-aliased payload must not put volts under a key
    # whose index is meant to sort ohms.
    result = normalise("resistor", "digikey", [("Resistance", "50 V")])

    # Assert
    spec = result.canonical["resistance"]
    assert spec.display == "50 V"
    assert spec.value_num is None
    assert spec.unit == "Ω"


def test_the_schema_table_cannot_be_mutated_by_an_importer() -> None:
    # Arrange / Act / Assert — process-wide state; one importer's edit would
    # change every workspace at once.
    with pytest.raises(TypeError):
        CANONICAL_SPECS["resistor"] = ()  # type: ignore[index]


# ---------------------------------------------------------------------------
# Common optional keys — physical dimensions, pin geometry, qualification
#
# These sit alongside `package` / `mounting` / `operating_temp` on EVERY
# category, including ones the schema does not model. They are what makes a
# connector or an IC row sortable at all.
# ---------------------------------------------------------------------------
def test_a_size_dimension_value_fills_both_the_length_and_the_width() -> None:
    # Arrange — one upstream key carrying two numbers. The only one-to-many
    # alias in the schema; see ADR-0034.
    payload = [("Size / Dimension", '0.126" L x 0.063" W (3.20mm x 1.60mm)')]

    # Act
    result = normalise("resistor", "digikey", payload)

    # Assert — metric equivalents, parsed, in the base unit.
    assert result.canonical["length"].display == "3.2 mm"
    assert result.canonical["length"].value_num == Decimal("0.00320")
    assert result.canonical["width"].display == "1.6 mm"
    assert result.canonical["width"].value_num == Decimal("0.00160")
    # One raw key, so it is a winner and never also a dropped alias.
    assert result.dropped == []
    assert result.optional == {}


def test_the_one_to_many_alias_is_still_one_key_in_the_payload() -> None:
    # Arrange / Act — the partition rule holds on the raw keys, which is
    # what "nothing is lost and nothing is duplicated" means here.
    payload = [("Size / Dimension", '0.126" L x 0.063" W (3.20mm x 1.60mm)')]
    result = normalise("resistor", "digikey", payload)

    # Assert
    raw_keys = {spec.raw_key for spec in result.canonical.values()}
    assert raw_keys | set(result.optional) | set(result.catalog) | set(
        result.dropped
    ) == {"Size / Dimension"}


def test_mouser_sends_length_and_width_as_two_keys_and_both_survive() -> None:
    # Arrange — "the last of one part" has to be that same part, or Mouser
    # loses its width.
    result = normalise("resistor", "mouser", [("Length", "3.2 mm"), ("Width", "1.6 mm")])

    # Assert
    assert result.canonical["length"].display == "3.2 mm"
    assert result.canonical["width"].display == "1.6 mm"


def test_an_imperial_height_is_stored_as_the_vendors_metric_equivalent() -> None:
    # Arrange / Act — `parse_si` has no inch entry and is not getting one;
    # the metric figure DigiKey already prints is used instead.
    result = normalise("resistor", "digikey", [("Height - Seated (Max)", '0.087" (2.20mm)')])

    # Assert
    assert result.canonical["height"].display == "2.2 mm"
    assert result.canonical["height"].value_num == Decimal("0.00220")


def test_a_lead_spacing_lands_on_the_common_pin_pitch_key() -> None:
    result = normalise("resistor", "digikey", [("Lead Spacing", '0.100" (2.54mm)')])

    assert result.canonical["pin_pitch"].display == "2.54 mm"


# ---------------------------------------------------------------------------
# `Ratings` — off the junk denylist, and worth exactly one fact
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key", ["Ratings", "Qualification"])
def test_the_qualification_keys_are_no_longer_junk(key: str) -> None:
    """They were denylisted because most of what they carry is prose. They
    now feed `automotive`, and the extractor is what drops the prose."""
    assert is_junk_key(key) is False


@pytest.mark.parametrize(
    ("provider", "key"), [("digikey", "Ratings"), ("mouser", "Qualification")]
)
def test_an_aec_rating_becomes_the_automotive_key(provider: str, key: str) -> None:
    result = normalise("resistor", provider, [(key, "AEC-Q200")])

    assert result.canonical["automotive"].display == "AEC-Q200"
    assert result.canonical["automotive"].raw_key == key


@pytest.mark.parametrize(
    ("provider", "key"), [("digikey", "Ratings"), ("mouser", "Qualification")]
)
def test_a_non_automotive_rating_is_dropped_rather_than_stored(
    provider: str, key: str
) -> None:
    """The whole point of taking the key off the denylist was the AEC-Q
    token. "Moisture Resistant" under it is the prose the denylist existed
    to refuse, and must not come back as a verbatim Specs row."""
    result = normalise("resistor", provider, [(key, "Moisture Resistant")])

    assert "automotive" not in result.canonical
    assert result.optional == {}
    assert result.dropped == [key]


def test_number_of_terminations_stays_junk() -> None:
    """It reads like a pin count and is not one: DigiKey files a two-pad
    chip resistor's `2` under it. `pin_count` takes `Number of Pins` only,
    so a passive does not acquire a pin count it has no use for."""
    assert is_junk_key("Number of Terminations") is True
    result = normalise("resistor", "digikey", [("Number of Terminations", "2")])
    assert result.canonical == {}
    assert result.dropped == ["Number of Terminations"]


def test_a_number_of_pins_is_a_pin_count() -> None:
    # Arrange / Act — a count has no unit, so it is kept verbatim with no
    # numeric sidecar, the way `hfe` and `unidirectional` already are.
    result = normalise("resistor", "digikey", [("Number of Pins", "8")])

    # Assert
    assert result.canonical["pin_count"].display == "8"
    assert result.canonical["pin_count"].value_num is None
