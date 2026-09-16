"""Provider category strings -> our category name paths (A4).

118 of 324 prod parts have no category because provider import never
assigned one, and a part with no category gets the common spec schema
only — no resistance, no capacitance, nothing to render a KiCad `Value`
from. This module is the rule table that closes that gap.

It is deliberately thin. `spec_schema.category_slug_for` already turns
free text into a schema slug with the two-pass class-then-modifier rule
ADR-0034 pins (the component noun fixes the class, a modifier refines
it), and every DigiKey and Mouser category name we care about is
classified correctly by it. So the mapping here is
`text -> slug -> our path`, plus two things the slug pass cannot know:

* **what to refuse.** "Resistor Networks, Arrays" classifies as a
  resistor because "Resistor" is a component noun, but an array has no
  single resistance — filing it under Resistors would make every
  mandatory key on it permanently missing. `_EXCLUDED_WORDS` refuses
  those outright rather than filing them wrong.
* **which text to trust, per provider.** DigiKey's `Category.Name` is
  the leaf of its taxonomy and its description is a terse part string
  ("RES SMD 10K OHM 1% 1/16W 0402") that names no class, so only the
  category is read. Mouser's `Category` is coarse and often absent while
  its description carries the class verbatim ("Thick Film Resistors -
  SMD General Purpose Chip Resistor 0402, …") — the same string
  `providers/mouser.py` already mines for specs — so the description is
  read as a fallback there and nowhere else.

Pure data + one function: no DB, no workspace, no I/O. Turning a path
into a row of `part_categories` is `categories/service.py::
resolve_category_path`, which never creates anything.
"""
from __future__ import annotations

from app.domain.parts.spec_schema_tables import PATH_SEPARATOR_RE, WORD_RE

__all__ = ["CATEGORY_PATH_FOR_SLUG", "category_for_provider"]


# Schema slug -> the ` / `-joined category name path this workspace's tree
# is expected to use. The separator matches `kicad_library.py`'s category
# naming (B5) and `resolve_category_path` splits on it.
#
# Two deliberate omissions, both because the provider's category string
# does not carry the fact:
#
# * MOSFET and BJT stop at the class. N- vs P-channel and NPN vs PNP are
#   in the `fet_type` / `transistor_type` SPEC, not in
#   "Transistors - FETs, MOSFETs - Single" or "MOSFET". Returning
#   "Transistors / MOSFET N" would be a coin flip on half the parts, and
#   a wrong category is worse than a missing one — the root fallback in
#   `resolve_category_path_or_root` still files the part under
#   Transistors.
# * Schottky has a slug of its own because the *word* appears in vendor
#   category names ("Diodes - Rectifiers - Schottky"); a plain rectifier
#   maps to the Diodes root rather than guessing at a sub-category that
#   the seed (A6) has not created yet.
CATEGORY_PATH_FOR_SLUG: dict[str, str] = {
    "resistor": "Resistors",
    "capacitor_ceramic": "Capacitors / Ceramic",
    "capacitor_electrolytic": "Capacitors / Electrolytic",
    "capacitor_tantalum": "Capacitors / Tantalum",
    "capacitor_film": "Capacitors / Film",
    "inductor": "Inductors",
    "diode": "Diodes",
    "diode_schottky": "Diodes / Schottky",
    "diode_zener": "Diodes / Zener",
    "diode_tvs": "Diodes / TVS",
    "led": "Diodes / LED",
    "transistor_bjt": "Transistors / BJT",
    "transistor_mosfet": "Transistors / MOSFET",
}

# Words that mean "several of these in one package", which is a different
# part from the one the component noun names. They win over the class
# because they are nouns too, and the schema has no key that survives the
# plural ("resistance" of a 4-way array is four numbers).
_EXCLUDED_WORDS: frozenset[str] = frozenset({
    "array",
    "arrays",
    "assortment",
    "assortments",
    "kit",
    "kits",
    "network",
    "networks",
})

# Providers whose description is worth classifying. See the module
# docstring: this is a statement about the two vendors' data, not a
# capability check, so an unknown provider simply doesn't get the
# fallback.
_DESCRIPTION_CLASSIFIED_PROVIDERS: frozenset[str] = frozenset({"mouser"})


def category_for_provider(
    provider_name: str | None,
    provider_category: str | None,
    description: str | None = None,
) -> str | None:
    """Our category name path for one provider payload, or ``None``.

    ``None`` means "no confident answer" and is the common case for ICs,
    connectors, crystals and every other family the spec schema does not
    model. Callers surface it as a suggestion; they never invent a
    category from it.
    """
    path = _path_for_text(provider_category)
    if path is not None:
        return path
    if (provider_name or "").strip().lower() in _DESCRIPTION_CLASSIFIED_PROVIDERS:
        return _path_for_text(description)
    return None


def _path_for_text(text: str | None) -> str | None:
    # Imported here rather than at module scope: `spec_schema` re-exports
    # `category_for_provider`, so a module-level import back into it would
    # be circular.
    from app.domain.parts.spec_schema import category_slug_for

    if not text or not text.strip():
        return None
    words = set(WORD_RE.findall(PATH_SEPARATOR_RE.sub(" ", text.lower())))
    if words & _EXCLUDED_WORDS:
        return None
    slug = category_slug_for(text)
    return CATEGORY_PATH_FOR_SLUG.get(slug) if slug else None
