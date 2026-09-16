"""Render a part category's `value_template` against one part's specs.

A KiCad schematic shows a symbol's `Value`, and for a passive that is
the only thing on it worth reading: `10 kΩ 1% 0603`, not
`RES SMD 10K OHM 1% 1/16W 0402`. Parts imported from a provider get the
provider's description as their name, so `Value` inherited the marketing
copy. A per-category template turns the canonical specs the part already
carries into the string an engineer expects.

The template lives on `part_categories.value_template`; the specs are
`custom_fields` rows on the part. Everything here is a pure function of
those two, so `kicad_library.py` can batch the lookups and the rules are
testable without a database.

Three rules, in the order they bite:

* **A missing spec takes its whitespace with it.** A template is written
  for a fully specified part, and half the library is half-specified.
  `{resistance} {tolerance} {package}` on a part with no tolerance has
  to read `10 kΩ 0603`, so the substitution is followed by a whitespace
  collapse rather than by a per-placeholder dance.
* **`{mpn}` is not a spec.** It is the sensible default template for
  everything that is not a passive, and it resolves from the part's own
  column — never from a custom field that happens to be called `mpn`,
  which a provider could write.
* **An empty render is None.** The caller falls through to the part
  name. Returning `""` would put an empty `Value` on the symbol, and an
  empty KiCad field is not nothing — it is a blank property drawn on
  every instance of it.

Only `{[a-z_]+}` is a placeholder. Anything else between braces is
literal text: `domain/categories/schemas.py` refuses it on the way in,
and rendering a row that reached the column some other way must not
raise.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

__all__ = [
    "MAX_VALUE_LENGTH",
    "MPN_PLACEHOLDER",
    "PLACEHOLDER_PATTERN",
    "placeholder_keys",
    "render_value",
    "spec_field_label",
]

# A canonical spec key in braces. Lower-case and underscores only.
#
# Deliberately narrow: a provider's verbatim attribute name is whatever
# the vendor typed (`Power (Watts)`, `Voltage - Rated`) and is not
# expressible here. Canonical keys are what the spec-normalisation work
# writes; until a workspace's rows carry them, a template renders
# nothing for those parts and the `Value` falls back to `parts.name` —
# the behaviour this feature replaces, not a regression.
#
# `domain/categories/schemas.py::PLACEHOLDER_PATTERN` is a copy, so the
# column's validator can refuse a malformed template without `categories`
# importing `eda`. `tests/test_value_template.py` pins them equal.
PLACEHOLDER_PATTERN = re.compile(r"\{([a-z_]+)\}")

# The one placeholder resolved from the part rather than from its specs.
MPN_PLACEHOLDER = "mpn"

# A rendered `Value` past this is refused rather than truncated: the
# string is drawn on the schematic, and half a unit (`4.7 µ`) is worse
# than falling back to the part name. Reachable — `custom_fields.value`
# is `String(1024)`, so a single placeholder can blow past this on its
# own — which is why the check is here and not an assertion.
MAX_VALUE_LENGTH = 200


def placeholder_keys(template: str | None) -> set[str]:
    """The custom-field keys `template` reads.

    `{mpn}` is excluded — it is a part column, so a caller batching a
    `custom_fields` query must not ask for it.
    """
    if not template:
        return set()
    return {
        key
        for key in PLACEHOLDER_PATTERN.findall(template)
        if key != MPN_PLACEHOLDER
    }


def render_value(
    template: str | None, specs: Mapping[str, str], mpn: str | None
) -> str | None:
    """The `Value` this template renders for one part, or None.

    None whenever there is nothing to show: no template, or every
    placeholder in it resolved to nothing, or a render too long to draw.
    """
    if not template:
        return None

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        raw = mpn if key == MPN_PLACEHOLDER else specs.get(key)
        return (raw or "").strip()

    # Collapse after substituting, not around each placeholder: a
    # dropped middle placeholder leaves two spaces and a dropped end one
    # leaves a trailing space, and `split()` handles both without the
    # template having to be walked twice.
    rendered = " ".join(PLACEHOLDER_PATTERN.sub(substitute, template).split())
    if not rendered or len(rendered) > MAX_VALUE_LENGTH:
        return None
    return rendered


def spec_field_label(key: str) -> str:
    """The KiCad symbol-field name a canonical spec key is emitted under.

    `voltage_rating` → `Voltage Rating`. KiCad field names are shown to
    the user in the symbol properties dialog, so they are title-cased
    rather than passed through as the snake_case the database uses.
    """
    return key.replace("_", " ").title()
