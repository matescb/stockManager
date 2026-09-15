"""`domain/eda/value_template.py` — rendering a category's Value template.

Pure functions over a dict, so this file needs no database. The rules
worth pinning are the ones a user will hit on their first template:

* a placeholder whose spec is missing takes its surrounding whitespace
  with it, so `{resistance} {tolerance} {package}` on a part with no
  tolerance reads `10 kΩ 0603` and not `10 kΩ  0603`;
* `{mpn}` is the one placeholder that is not a spec;
* an empty render is None, never `""` — the caller falls through to the
  part name, and an empty KiCad `Value` is a blank property drawn on
  every instance of the symbol.
"""
from __future__ import annotations

import pytest

from app.domain.eda.value_template import (
    MAX_VALUE_LENGTH,
    placeholder_keys,
    render_value,
    spec_field_label,
)

SPECS = {
    "resistance": "10 kΩ",
    "tolerance": "1%",
    "package": "0603",
    "power": "100 mW",
}


# ---------------------------------------------------------------------
# render_value
# ---------------------------------------------------------------------


def test_placeholders_are_replaced_by_their_spec_values():
    assert (
        render_value("{resistance} {tolerance} {package}", SPECS, None)
        == "10 kΩ 1% 0603"
    )


def test_a_missing_spec_takes_its_surrounding_space_with_it():
    """The whole point of the collapse: a template written for a fully
    specified part must still read well on a half-specified one."""
    specs = {"resistance": "10 kΩ", "package": "0603"}
    assert render_value("{resistance} {tolerance} {package}", specs, None) == "10 kΩ 0603"


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("{tolerance} {package}", "0603"),
        ("{resistance} {tolerance}", "10 kΩ"),
        ("{tolerance}", None),
    ],
)
def test_a_missing_spec_at_either_end_leaves_no_stray_whitespace(
    template: str, expected: str | None
):
    specs = {"resistance": "10 kΩ", "package": "0603"}
    assert render_value(template, specs, None) == expected


def test_literal_text_between_placeholders_is_kept():
    assert render_value("R {resistance} ±{tolerance}", SPECS, None) == "R 10 kΩ ±1%"


def test_mpn_is_the_one_placeholder_that_is_not_a_spec():
    assert render_value("{mpn}", {}, "RC0402FR-0710KL") == "RC0402FR-0710KL"
    assert render_value("{package} {mpn}", SPECS, "ABC-1") == "0603 ABC-1"


def test_mpn_is_dropped_like_any_other_when_the_part_has_none():
    assert render_value("{package} {mpn}", SPECS, None) == "0603"
    assert render_value("{package} {mpn}", SPECS, "") == "0603"


def test_a_spec_key_named_mpn_does_not_shadow_the_part_mpn():
    """`mpn` is resolved from the part, never from a custom field —
    otherwise a provider row called `mpn` would silently win."""
    assert render_value("{mpn}", {"mpn": "FROM-SPEC"}, "FROM-PART") == "FROM-PART"


@pytest.mark.parametrize("template", [None, "", "   ", "{unknown}", "{a} {b}"])
def test_nothing_to_render_is_none(template: str | None):
    assert render_value(template, {}, None) is None


def test_whitespace_only_specs_count_as_missing():
    assert render_value("{resistance} {package}", {"package": "   "}, None) is None


def test_internal_whitespace_in_the_template_is_collapsed():
    assert render_value("{resistance}    {package}", SPECS, None) == "10 kΩ 0603"
    assert render_value("  {package}  ", SPECS, None) == "0603"


def test_spec_values_are_stripped_of_surrounding_whitespace():
    """Provider values arrive padded often enough that not stripping
    them would show up as a double space in half the renders."""
    assert render_value("{resistance}", {"resistance": "  4.7 µF  "}, None) == "4.7 µF"


def test_a_render_past_the_cap_is_refused_rather_than_truncated():
    """A KiCad `Value` is drawn on the schematic. Half a unit (`4.7 µ`)
    is worse than falling back to the part name, so the cap returns
    None rather than cutting."""
    specs = {"resistance": "x" * MAX_VALUE_LENGTH, "package": "0603"}
    assert render_value("{resistance} {package}", specs, None) is None


def test_a_render_exactly_at_the_cap_is_kept():
    specs = {"resistance": "x" * MAX_VALUE_LENGTH}
    assert render_value("{resistance}", specs, None) == "x" * MAX_VALUE_LENGTH


def test_braces_that_are_not_placeholders_are_left_alone():
    """The schema validator refuses these on the way in; rendering one
    that reached the column anyway must not raise."""
    assert render_value("{Resistance}", SPECS, None) == "{Resistance}"
    assert render_value("{ resistance }", SPECS, None) == "{ resistance }"
    assert render_value("{}", SPECS, None) == "{}"


# ---------------------------------------------------------------------
# placeholder_keys
# ---------------------------------------------------------------------


def test_placeholder_keys_lists_every_spec_the_template_reads():
    assert placeholder_keys("{resistance} {tolerance} {package}") == {
        "resistance",
        "tolerance",
        "package",
    }


def test_placeholder_keys_excludes_mpn_because_it_is_not_a_custom_field():
    assert placeholder_keys("{mpn} {package}") == {"package"}


@pytest.mark.parametrize("template", [None, "", "no placeholders", "{Nope}"])
def test_placeholder_keys_of_nothing_is_empty(template: str | None):
    assert placeholder_keys(template) == set()


# ---------------------------------------------------------------------
# spec_field_label
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "label"),
    [
        ("resistance", "Resistance"),
        ("voltage_rating", "Voltage Rating"),
        ("rds_on", "Rds On"),
        ("vz", "Vz"),
        ("id_max", "Id Max"),
    ],
)
def test_spec_field_label_title_cases_the_canonical_key(key: str, label: str):
    assert spec_field_label(key) == label
