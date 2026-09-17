"""`domain/parts/spec_extract.py` — the per-key value extractors.

Three canonical keys are not about the whole provider value: `length` and
`width` share one `Size / Dimension` string, dimensions arrive in inches
with a metric equivalent in parentheses, and `Ratings` is a prose list of
which only the AEC-Q qualification is a spec.

Two properties matter as much as the happy path and are pinned here:
an extractor never raises, and it is **idempotent on its own output** —
the `spec-normalize` backfill re-reads what it wrote through
`spec_schema.canonical_value`, which runs the extractor again.
"""
from __future__ import annotations

import pytest

from app.domain.parts.spec_extract import EXTRACTORS, extract_for


# ---------------------------------------------------------------------------
# metric — prefer the parenthesised metric equivalent
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # DigiKey's own spelling: imperial first, metric in parentheses.
        ('0.087" (2.20mm)', "2.20mm"),
        ('0.138" (3.50mm)', "3.50mm"),
        ('0.100" (2.54mm)', "2.54mm"),
        # Already metric, no parenthetical — unchanged.
        ("2.2 mm", "2.2 mm"),
        ("1.27mm", "1.27mm"),
        # A parenthetical that is not a metric equivalent is left alone;
        # "1005 Metric" is a package-code note, not a millimetre figure.
        ("0402 (1005 Metric)", "0402 (1005 Metric)"),
    ],
)
def test_metric_prefers_the_parenthesised_millimetre_figure(
    raw: str, expected: str
) -> None:
    assert extract_for("metric", raw) == expected


# ---------------------------------------------------------------------------
# dimension_first / dimension_last — one raw key, two canonical keys
# ---------------------------------------------------------------------------
_TWO_DIMENSIONS = '0.126" L x 0.063" W (3.20mm x 1.60mm)'


def test_a_size_dimension_value_yields_a_length_and_a_width() -> None:
    # Arrange / Act — the one place in the schema where a single upstream
    # key feeds two canonical keys.
    assert extract_for("dimension_first", _TWO_DIMENSIONS) == "3.20mm"
    assert extract_for("dimension_last", _TWO_DIMENSIONS) == "1.60mm"


def test_a_single_dimension_is_both_the_length_and_the_width() -> None:
    # Arrange / Act — a round part: DigiKey sends one diameter.
    assert extract_for("dimension_first", '0.063" Dia (1.60mm)') == "1.60mm"
    assert extract_for("dimension_last", '0.063" Dia (1.60mm)') == "1.60mm"


@pytest.mark.parametrize("name", ["dimension_first", "dimension_last"])
def test_a_plain_mouser_dimension_survives_both_extractors(name: str) -> None:
    """Mouser sends `Length` and `Width` as separate keys with one value
    each. Taking "the first of one part" and "the last of one part" has to
    be that same value, or one of the two keys loses its number."""
    assert extract_for(name, "3.2 mm") == "3.2 mm"


def test_an_x_inside_a_word_is_not_a_dimension_separator() -> None:
    # Arrange / Act — splitting on a bare `x` would cut "Max" in half and
    # leave "3.2 mm Ma", which parses as nothing.
    assert extract_for("dimension_first", "3.2 mm Max") == "3.2 mm Max"


# ---------------------------------------------------------------------------
# aec_qualification — the only spec inside `Ratings`
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AEC-Q200", "AEC-Q200"),
        ("AEC-Q101", "AEC-Q101"),
        ("Automotive AEC-Q200", "AEC-Q200"),
        ("AEC-Q100, Automotive", "AEC-Q100"),
        ("aec-q200", "AEC-Q200"),
        ("AEC Q200", "AEC-Q200"),
    ],
)
def test_aec_qualification_finds_the_qualification_anywhere_in_the_value(
    raw: str, expected: str
) -> None:
    assert extract_for("aec_qualification", raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "Moisture Resistant",
        "Automotive",
        "General Purpose",
        "RoHS Compliant",
    ],
)
def test_a_ratings_value_without_a_qualification_says_nothing(raw: str) -> None:
    """`Ratings` was on the junk denylist because most of what it carries
    is prose. ``None`` is how an extractor says "not a fact about this
    key" — `normalise()` drops the alias instead of keeping the prose."""
    assert extract_for("aec_qualification", raw) is None


# ---------------------------------------------------------------------------
# Total, and idempotent on their own output
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(EXTRACTORS))
@pytest.mark.parametrize(
    "raw",
    ["", "   ", "-", "()", "x", " x ", "(mm)", "AEC-Q", "—", "a" * 500],
)
def test_no_extractor_raises_on_hostile_input(name: str, raw: str) -> None:
    result = extract_for(name, raw)

    assert result is None or isinstance(result, str)


@pytest.mark.parametrize("name", sorted(EXTRACTORS))
@pytest.mark.parametrize(
    "raw",
    [
        '0.126" L x 0.063" W (3.20mm x 1.60mm)',
        '0.087" (2.20mm)',
        "2.2 mm",
        "AEC-Q200",
        "Surface Mount",
    ],
)
def test_every_extractor_is_idempotent_on_its_own_output(
    name: str, raw: str
) -> None:
    """`spec-normalize` re-reads its own rows through `canonical_value`,
    which runs the extractor a second time. A second pass that moved the
    value would make the job rewrite every row on every run."""
    once = extract_for(name, raw)
    if once is None:
        return

    assert extract_for(name, once) == once


def test_an_unknown_extractor_name_is_a_programming_error() -> None:
    """The name comes from the schema table, not from user input, so a
    typo must fail loudly at the call rather than quietly drop a key."""
    with pytest.raises(KeyError):
        extract_for("not_an_extractor", "10")
