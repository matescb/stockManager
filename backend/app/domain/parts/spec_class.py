"""The class-level view of the spec schema: what a bare "Capacitors" has.

`spec_schema.category_slug_for` answers in two passes — the component
noun fixes the CLASS, then a modifier refines it to a SLUG — and for two
classes the second pass has no honest default. `CLASS_DEFAULT_SLUG` is
``None`` for `capacitor` and `transistor` because a capacitor with no
dielectric named has no single key set: `dielectric` belongs to ceramics
and films, `esr` to electrolytics and tantalums, and putting either on
the other is a lie.

That is still the right answer for *writing* a part's specs. It is the
wrong answer for *reading* a category, which is what this module exists
for. The parts filed under a bare root were written by whichever slug
their own import resolved, so the branch genuinely holds `capacitance`,
`dielectric` and `esr` rows — and a columns menu built from
`spec_keys_for(None)` offers none of them. So a root that maps to a
class but to no slug reads as the UNION of its class's slugs:

    component_class_for("Capacitors")  -> "capacitor"
    class_slugs("capacitor")           -> the four dielectric slugs
    union_spec_keys(those)             -> their keys, deduplicated

The union is a read-side vocabulary only. Nothing here changes what
`normalise()` writes, which slug a part resolves to, or which keys
`missing_mandatory` demands — a key that only some of the class's slugs
define comes back NOT mandatory, precisely so the completeness badge
does not start failing every ceramic in the branch.

See ADR-0034; the data lives in `spec_schema_tables.py`.
"""
from __future__ import annotations

from dataclasses import replace

from app.domain.parts.spec_key import SpecKey
from app.domain.parts.spec_schema_tables import (
    CLASS_DEFAULT_SLUG,
    CLASS_RULES,
    PATH_SEPARATOR_RE,
    REFINEMENT_RULES,
    WORD_RE,
)

__all__ = ["class_slugs", "component_class_for", "union_spec_keys"]


def component_class_for(category_name_path: str | None) -> str | None:
    """The FIRST pass of `category_slug_for` — the component noun only.

    Split out rather than duplicated: the class table is `CLASS_RULES`,
    the same tuple that decides the slug, so a new class noun teaches
    both at once. `category_slug_for` calls this for its first pass.
    """
    if not category_name_path:
        return None
    flattened = PATH_SEPARATOR_RE.sub(" ", category_name_path).lower()
    words = set(WORD_RE.findall(flattened))
    return next((name for triggers, name in CLASS_RULES if words & triggers), None)


def class_slugs(component_class: str) -> tuple[str, ...]:
    """Every schema slug a class can refine to, in refinement-rule order.

    The class's own default is included when it has one, last, so a class
    that both refines and defaults (none today) would not lose its
    fallback keys. Deduplicated because `REFINEMENT_RULES` may name the
    same slug twice and because the default is often one of the
    refinements.

    A slug that IS another class's own default is dropped: the `diode`
    rules refine to `led`, which is a class in its own right with its own
    root category, and a bare "Diodes" must not start offering an LED's
    colour and luminous intensity.
    """
    refinements = tuple(
        slug
        for _, slug in REFINEMENT_RULES.get(component_class, ())
        if CLASS_DEFAULT_SLUG.get(slug) != slug
    )
    default = CLASS_DEFAULT_SLUG.get(component_class)
    ordered = refinements + ((default,) if default else ())
    return tuple(dict.fromkeys(ordered))


def union_spec_keys(
    slugs: tuple[str, ...],
) -> tuple[tuple[SpecKey, ...], dict[str, tuple[str, ...]]]:
    """`(keys, key -> defining slugs)` for a whole class, deduplicated.

    Order is stable and deliberate, because it is the order the columns
    menu lists:

      1. the common keys, in `COMMON_SPECS` order — every slug carries
         them and every category's menu has always started with them;
      2. the keys EVERY slug in the class defines (`capacitance`,
         `voltage_rating`), which are the ones that mean the same thing
         whatever the part under the root turns out to be;
      3. everything else in first-seen schema order, so a reader of
         `spec_schema_tables.py` can predict the menu.

    `mandatory` survives only when every slug in the class declares it
    mandatory — which a key that only some slugs define never does. The
    `SpecKey` otherwise comes from the first slug that defines it;
    `tests/test_spec_schema.py` pins that one key never carries two
    labels or two units.
    """
    # Imported here rather than at module scope: `spec_schema` imports
    # this module for `component_class_for`, so a top-level import back
    # would be a cycle. The table modules it reads are pure data.
    from app.domain.parts.spec_schema import spec_keys_for

    if not slugs:
        return spec_keys_for(None), {}

    first_seen: dict[str, SpecKey] = {}
    owners: dict[str, list[str]] = {}
    mandatory_count: dict[str, int] = {}
    for slug in slugs:
        for spec in spec_keys_for(slug):
            first_seen.setdefault(spec.key, spec)
            owners.setdefault(spec.key, []).append(slug)
            mandatory_count[spec.key] = mandatory_count.get(spec.key, 0) + spec.mandatory

    common = frozenset(spec.key for spec in spec_keys_for(None))

    def everywhere(key: str) -> bool:
        return len(owners[key]) == len(slugs)

    def bucket(key: str) -> int:
        if key in common:
            return 0
        return 1 if everywhere(key) else 2

    # `sorted` is stable, so within a bucket the keys keep the order they
    # were first seen in — which for the common ones is `COMMON_SPECS`
    # order, since every slug starts with it.
    keys = tuple(
        replace(
            first_seen[key],
            mandatory=everywhere(key) and mandatory_count[key] == len(slugs),
        )
        for key in sorted(first_seen, key=bucket)
    )
    return keys, {key: tuple(owners[key]) for key in first_seen}
