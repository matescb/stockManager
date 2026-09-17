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


# Category slug -> common keys that category answers with one of its own.
#
# `spec_keys_for` drops these from the common set for that slug. Without
# it a connector payload's `Pitch` would fill BOTH the common `pin_pitch`
# and the connector's `pitch` — two canonical rows for one fact, which is
# the thing ADR-0034 forbids most plainly.
COMMON_OVERRIDES: dict[str, frozenset[str]] = {}


# Category slug -> the keys that category adds on top of the common set.
MORE_CANONICAL_SPECS: dict[str, tuple[SpecKey, ...]] = {}
