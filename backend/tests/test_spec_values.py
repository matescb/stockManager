"""`domain/parts/spec_values.py` — SI value parsing and canonical display.

Provider spec values arrive as free text in a dozen shapes for the same
quantity (`10k`, `10 kOhms`, `26mOhm Max`, `0.063W, 1/16W`). These tests
pin the forms actually observed in DigiKey `Parameters[]` and Mouser
description tokens, plus the refusals — the parser must never raise and
must return ``None`` rather than guess.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.parts.spec_values import ParsedValue, format_si, parse_si


# ---------------------------------------------------------------------------
# Happy path — (text, unit_hint, value_num, unit, display)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "hint", "expected_num", "expected_unit", "expected_display"),
    [
        # Bare magnitude + SI prefix, unit supplied by the schema.
        ("10k", "Ω", Decimal("10000"), "Ω", "10 kΩ"),
        ("10", "Ω", Decimal("10"), "Ω", "10 Ω"),
        # Spelled-out ohms, both cases, with and without the plural.
        ("10 kOhms", None, Decimal("10000"), "Ω", "10 kΩ"),
        ("10 kOhm", None, Decimal("10000"), "Ω", "10 kΩ"),
        ("1 MOhms", None, Decimal("1000000"), "Ω", "1 MΩ"),
        ("0 Ohms", None, Decimal("0"), "Ω", "0 Ω"),
        ("4.7 Ω", None, Decimal("4.7"), "Ω", "4.7 Ω"),
        # Micro in all three spellings (ASCII u, MICRO SIGN, GREEK MU).
        ("4.7 µF", None, Decimal("0.0000047"), "F", "4.7 µF"),
        ("4.7uF", None, Decimal("0.0000047"), "F", "4.7 µF"),
        ("4.7 μF", None, Decimal("0.0000047"), "F", "4.7 µF"),
        # Engineering re-scaling: 1e-7 F is 100 nF, not 0.1 µF.
        ("100nF", None, Decimal("0.0000001"), "F", "100 nF"),
        ("22 µH", None, Decimal("0.000022"), "H", "22 µH"),
        ("10 pF", None, Decimal("1E-11"), "F", "10 pF"),
        # Multi-valued power: take the leading term.
        ("0.063W, 1/16W", None, Decimal("0.063"), "W", "63 mW"),
        # Fractional watts, the way every chip-resistor datasheet writes it.
        ("1/4 W", None, Decimal("0.25"), "W", "250 mW"),
        ("1/16W", None, Decimal("0.0625"), "W", "62.5 mW"),
        # Voltage, with and without the space and the DC suffix.
        ("50V", None, Decimal("50"), "V", "50 V"),
        ("50 V", None, Decimal("50"), "V", "50 V"),
        ("50 VDC", None, Decimal("50"), "V", "50 V"),
        # Tolerance — the ± is noise, and % never takes an SI prefix.
        ("±1%", None, Decimal("1"), "%", "1%"),
        ("1%", None, Decimal("1"), "%", "1%"),
        ("+/-0.5 %", None, Decimal("0.5"), "%", "0.5%"),
        # Temperature coefficient.
        ("±100ppm/°C", None, Decimal("100"), "ppm/°C", "100 ppm/°C"),
        ("100 ppm/C", None, Decimal("100"), "ppm/°C", "100 ppm/°C"),
        # Trailing qualifier words are dropped, not parsed.
        ("26mOhm Max", None, Decimal("0.026"), "Ω", "26 mΩ"),
        ("1.2 V (Typ)", None, Decimal("1.2"), "V", "1.2 V"),
        # "@ condition" suffix: keep the leading number + unit.
        ("1.8 A @ 100 kHz", None, Decimal("1.8"), "A", "1.8 A"),
        ("30 mA", None, Decimal("0.03"), "A", "30 mA"),
        # Wavelength / luminous intensity (nano-metre, milli-candela).
        ("625nm", None, Decimal("6.25E-7"), "m", "625 nm"),
        ("2000 mcd", None, Decimal("2"), "cd", "2 cd"),
    ],
)
def test_parse_si_known_forms(
    text: str,
    hint: str | None,
    expected_num: Decimal,
    expected_unit: str,
    expected_display: str,
) -> None:
    # Arrange / Act
    parsed = parse_si(text, unit_hint=hint)

    # Assert
    assert parsed is not None, text
    assert parsed.value_num == expected_num
    assert parsed.unit == expected_unit
    assert parsed.display == expected_display


def test_parse_si_range_returns_display_without_a_number() -> None:
    # Arrange / Act — an operating-temperature range has no single value.
    parsed = parse_si("-55°C ~ 125°C")

    # Assert
    assert parsed == ParsedValue(value_num=None, unit="°C", display="-55°C ~ 125°C")


@pytest.mark.parametrize(
    "text",
    ["-55°C~125°C", "-55 °C ~ 125 °C", "-55°C to 125°C"],
)
def test_parse_si_range_spellings_normalise_to_one_display(text: str) -> None:
    parsed = parse_si(text)

    assert parsed is not None
    assert parsed.value_num is None
    assert parsed.display == "-55°C ~ 125°C"


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "-",
        "X7R",
        "Surface Mount",
        "0402",  # a package code is a number with no unit and no hint
        "N-Channel",
        "Bulk",
        "@ 100 kHz",
    ],
)
def test_parse_si_refuses_what_it_cannot_read(text: str | None) -> None:
    assert parse_si(text) is None


def test_parse_si_never_raises_on_hostile_input() -> None:
    # Arrange — values seen in the wild plus deliberate garbage.
    for text in ("1" * 400, "1e999999 V", "//// W", "±±±", "10 kΩ ~", "1/0 W"):
        # Act / Assert — a refusal is fine, an exception is not.
        assert parse_si(text) is None or isinstance(parse_si(text), ParsedValue)


def test_parse_si_keeps_full_precision_in_the_numeric_sidecar() -> None:
    # Arrange / Act — display rounds, value_num must not.
    parsed = parse_si("4.7 µF")

    # Assert — exact base-unit farads, so a DB sort is exact.
    assert parsed is not None
    assert parsed.value_num == Decimal("0.0000047")


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (Decimal("10000"), "Ω", "10 kΩ"),
        (Decimal("0.0000047"), "F", "4.7 µF"),
        (Decimal("0"), "V", "0 V"),
        (Decimal("1"), "%", "1%"),
        (Decimal("1234"), "Ω", "1.234 kΩ"),
        (Decimal("0.000000000001"), "F", "1 pF"),
    ],
)
def test_format_si_renders_engineering_notation(
    value: Decimal, unit: str, expected: str
) -> None:
    assert format_si(value, unit) == expected


def test_format_si_leaves_an_unknown_unit_alone() -> None:
    # Arrange / Act — no scaling table for "widgets", so no prefix.
    assert format_si(Decimal("1500"), "widgets") == "1500 widgets"


# ---------------------------------------------------------------------------
# Regressions from review — each of these silently returned a wrong number
# or a wrong refusal.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "hint", "expected_num", "expected_display"),
    [
        # `M` (mega) case-folds onto `m` (metre). Reading `10M` as ten metres
        # is a factor of a million under every resistance key.
        ("10M", "Ω", Decimal("10000000"), "10 MΩ"),
        ("10 M", "Ω", Decimal("10000000"), "10 MΩ"),
        ("4.7M", "Ω", Decimal("4700000"), "4.7 MΩ"),
        ("10m", "Ω", Decimal("0.01"), "10 mΩ"),
        # Thousands grouping is one number, not two values.
        ("1,000", "Ω", Decimal("1000"), "1 kΩ"),
        ("12,500 mA", None, Decimal("12.5"), "12.5 A"),
    ],
)
def test_parse_si_regressions(
    text: str, hint: str | None, expected_num: Decimal, expected_display: str
) -> None:
    parsed = parse_si(text, unit_hint=hint)

    assert parsed is not None, text
    assert parsed.value_num == expected_num
    assert parsed.display == expected_display


@pytest.mark.parametrize(
    ("text", "hint"),
    [
        ("10M", None),   # mega of nothing is not a quantity
        ("10k", None),
        ("5 k", "%"),    # `%` never scales, so this is unreadable, not 5000%
        ("5 k", "°C"),
        ("4,7 uF", "F"),  # a decimal comma: refuse rather than read as 4 F
    ],
)
def test_parse_si_refuses_rather_than_guessing(text: str, hint: str | None) -> None:
    assert parse_si(text, unit_hint=hint) is None


def test_a_bare_prefix_that_is_also_the_hinted_unit_is_the_unit() -> None:
    # Arrange / Act — `625 m` under a metre key is metres, not milli-metres.
    parsed = parse_si("625 m", unit_hint="m")

    # Assert
    assert parsed is not None
    assert parsed.value_num == Decimal("625")


@pytest.mark.parametrize(
    "text",
    ["-55°C ~ 125°C (TA)", "-55 C to +125 C", "-55°C TO 125°C"],
)
def test_parse_si_reads_the_temperature_range_spellings_providers_use(text: str) -> None:
    parsed = parse_si(text, unit_hint="°C")

    assert parsed is not None
    assert parsed.value_num is None
    assert parsed.unit == "°C"


def test_format_si_does_not_render_a_non_number_as_zero() -> None:
    # Arrange / Act — Postgres NUMERIC stores 'NaN'; a round trip must not
    # come back as a confident "0 Ω".
    assert format_si(Decimal("NaN"), "Ω") == "NaN Ω"
    assert format_si(Decimal("Infinity"), "Ω") == "Infinity Ω"


def test_format_si_never_raises_on_an_extreme_exponent() -> None:
    assert isinstance(format_si(Decimal("1E+999999999"), "Ω"), str)


def test_format_si_carries_a_rounded_up_mantissa_into_the_next_decade() -> None:
    # Arrange / Act — 999.99999 trims to "1000", which belongs one decade up.
    assert format_si(Decimal("999.99999"), "Ω") == "1 kΩ"


def test_format_si_has_no_negative_zero() -> None:
    assert format_si(Decimal("-0"), "V") == "0 V"


@pytest.mark.parametrize(
    "text",
    ["9" * 25 + " Ohms", "1" * 30 + " F"],
)
def test_parse_si_refuses_a_magnitude_the_column_cannot_hold(text: str) -> None:
    # Arrange / Act / Assert — value_num is NUMERIC(36,18). An exaohm is not
    # a spec, and letting it through would be a DataError at INSERT.
    assert parse_si(text) is None


def test_parse_si_rounds_a_repeating_fraction_to_the_column_scale() -> None:
    # Arrange / Act — 1/3 carries 28 digits at default context precision.
    parsed = parse_si("1/3 W")

    # Assert — 18 fractional digits, the scale of `custom_fields.value_num`.
    assert parsed is not None
    assert parsed.value_num is not None
    assert -parsed.value_num.as_tuple().exponent <= 18
    assert parsed.display == "333.3333 mW"


def test_parse_si_keeps_a_sub_picofarad_exactly() -> None:
    parsed = parse_si("0.5 pF")

    assert parsed is not None
    assert parsed.value_num == Decimal("5E-13")
