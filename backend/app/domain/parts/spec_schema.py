"""Map one provider spec payload onto this workspace's canonical spec
schema, and say what was junk, what was catalog metadata, and what is
still missing.

Today every `Parameters[]` / `ProductAttributes` row is written verbatim
as a `custom_fields(source='provider')` row. That is why the Specs tab
carries TARIC and CNHTS customs codes, ~1,000 rows whose value is
literally `-`, and three different spellings of the same resistance.
`normalise()` is the single place that decides, per raw key:

    junk       -> dropped
    catalog    -> catalog   (Sourcing tab; stock, price, packaging)
    canonical  -> canonical (the per-category schema, parsed + formatted)
    anything   -> optional  (kept verbatim; ICs and connectors lose nothing)

`normalise()` has no side effects: no DB session, no workspace, no
outbound request. (It does import `providers/mouser.py` to reuse that
module's description miner rather than copy its regexes.) Writing the
result is the caller's job (A3). Cross-provider precedence for the same
canonical key is also the caller's job — `normalise()` sees exactly one
provider payload and says what that payload means.

See ADR-0034; the data lives in `spec_schema_tables.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Iterable, Sequence

from app.domain.parts.provider_fields import PROVIDER_RESERVED_CUSTOM_FIELD_KEYS
from app.domain.parts.providers.mouser import parse_description_specs
from app.domain.parts.spec_schema_tables import (
    CANONICAL_SPECS,
    CATALOG_KEY_PATTERNS,
    CATALOG_LITERAL_KEYS,
    CLASS_DEFAULT_SLUG,
    CLASS_RULES,
    COMMON_SPECS,
    DROP_KEY_PATTERNS,
    JUNK_VALUES,
    PATH_SEPARATOR_RE,
    REFINEMENT_RULES,
    WORD_RE,
    SpecKey,
)
from app.domain.parts.spec_values import parse_si

# Re-exported read-only: the schema is process-wide state and an importer
# that mutated it would change every workspace's behaviour at once.
CANONICAL_SPECS = MappingProxyType(dict(CANONICAL_SPECS))

__all__ = [
    "CANONICAL_SPECS",
    "CATALOG_KEY_PATTERNS",
    "CATALOG_LITERAL_KEYS",
    "DROP_KEY_PATTERNS",
    "NormalisedSpecs",
    "SpecKey",
    "SpecValue",
    "category_slug_for",
    "is_catalog_key",
    "is_junk_key",
    "is_junk_value",
    "missing_mandatory",
    "normalise",
    "spec_keys_for",
]


@dataclass(frozen=True)
class SpecValue:
    """One canonical spec, resolved from one provider payload."""

    key: str
    display: str
    value_num: Decimal | None
    unit: str | None
    raw_key: str
    raw_value: str


@dataclass(frozen=True)
class NormalisedSpecs:
    """The four buckets one provider payload sorts into.

    Every key in the payload lands in exactly one of them, with two
    documented exceptions: a pair whose key is blank or whitespace is
    ignored entirely, and a key repeated with two real values keeps the
    first and discards the rest silently.
    """

    canonical: dict[str, SpecValue]
    optional: dict[str, str]
    catalog: dict[str, str]
    #: Raw keys not surfaced verbatim: junk keys, junk values, and aliases
    #: superseded by a canonical key that a higher-precedence alias filled.
    dropped: list[str]


def spec_keys_for(category_slug: str | None) -> tuple[SpecKey, ...]:
    """Common keys plus the category's own. Unknown slug -> common only."""
    if not category_slug or category_slug == "common":
        return COMMON_SPECS
    return COMMON_SPECS + CANONICAL_SPECS.get(category_slug, ())


def is_junk_key(key: str) -> bool:
    key = (key or "").strip()
    return bool(key) and any(p.match(key) for p in DROP_KEY_PATTERNS)


def is_junk_value(value: str | None) -> bool:
    """`-`, empty and whitespace are placeholders, not data."""
    return (value or "").strip().lower() in JUNK_VALUES


def is_catalog_key(key: str) -> bool:
    key = (key or "").strip()
    if key in CATALOG_LITERAL_KEYS:
        return True
    return any(p.match(key) for p in CATALOG_KEY_PATTERNS)


def missing_mandatory(
    category_slug: str | None, canonical_keys_present: Iterable[str]
) -> list[str]:
    """Mandatory keys for the category that nobody supplied, in schema order."""
    present = set(canonical_keys_present)
    return [s.key for s in spec_keys_for(category_slug) if s.mandatory and s.key not in present]


def category_slug_for(category_name_path: str | None) -> str | None:
    """`"Capacitors / Ceramic"` -> `"capacitor_ceramic"`; unknown -> ``None``.

    Two passes over the words in the path: the component noun fixes the
    class, then a modifier refines it. Doing both in one flat pass reads
    `"Thick Film Resistors"` as a film capacitor, because `film` and
    `ceramic` are adjectives whose meaning depends on the class.

    Word-based rather than segment-based, so both `"Diodes / Zener"` and
    a flat `"Zener diodes"` land on the same slug.
    """
    if not category_name_path:
        return None
    flattened = PATH_SEPARATOR_RE.sub(" ", category_name_path).lower()
    words = set(WORD_RE.findall(flattened))
    component_class = next(
        (name for triggers, name in CLASS_RULES if words & triggers), None
    )
    if component_class is None:
        return None
    for triggers, slug in REFINEMENT_RULES.get(component_class, ()):
        if words & triggers:
            return slug
    return CLASS_DEFAULT_SLUG[component_class]


def normalise(
    category_slug: str | None,
    provider: str,
    raw_specs: Sequence[tuple[str, str]],
    *,
    description: str | None = None,
) -> NormalisedSpecs:
    """Classify one provider's spec payload for one part.

    `raw_specs` is the provider's `(key, value)` list in payload order.
    `description` is only consulted for Mouser, whose `ProductAttributes`
    is packaging trivia: the parametric values live in the description
    text and are mined by `providers/mouser.py::parse_description_specs`.
    A real attribute always beats a description-mined one for the same
    canonical key — that is a precedence rule, not a payload-order
    accident, because alias precedence is schema order.
    """
    attributes, dropped = _partition(raw_specs)
    mined: list[tuple[str, str]] = []
    if provider == "mouser" and description:
        known = {key for key, _ in attributes}
        mined_pairs, mined_dropped = _partition(parse_description_specs(description))
        mined = [(key, value) for key, value in mined_pairs if key not in known]
        dropped += [key for key in mined_dropped if key not in known]

    canonical, consumed = _resolve_canonical(
        category_slug, provider, dict(attributes), dict(mined)
    )
    winners = {value.raw_key for value in canonical.values()}
    # A superseded alias is represented by the canonical key it lost to, so
    # it is not surfaced verbatim either.
    dropped += [
        key for key, _ in attributes + mined if key in consumed and key not in winners
    ]

    catalog: dict[str, str] = {}
    optional: dict[str, str] = {}
    for key, value in attributes + mined:
        if key in consumed:
            continue
        if key in PROVIDER_RESERVED_CUSTOM_FIELD_KEYS or is_catalog_key(key):
            catalog[key] = value
        else:
            optional[key] = value
    return NormalisedSpecs(
        canonical=canonical, optional=optional, catalog=catalog, dropped=dropped
    )


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------
def _partition(raw_specs: Sequence[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[str]]:
    """Split a payload into keepable pairs and refused keys.

    One pass, so a key lands in exactly one of the two — the buckets
    `normalise` builds on top of them are disjoint by construction. A key
    repeated with both a junk and a real value (Mouser repeats
    `AttributeName` routinely, and ~1,000 prod rows have the value `-`)
    keeps its first real value rather than being dropped on the strength
    of the placeholder.
    """
    kept: dict[str, str] = {}
    order: list[str] = []
    for raw_key, raw_value in raw_specs:
        key = (raw_key or "").strip()
        if not key:
            continue
        if key not in order:
            order.append(key)
        if key in kept or is_junk_key(key):
            continue
        value = (raw_value or "").strip()
        if not is_junk_value(value):
            kept[key] = value
    return (
        [(key, kept[key]) for key in order if key in kept],
        [key for key in order if key not in kept],
    )


def _aliases_for(spec: SpecKey, provider: str) -> tuple[str, ...]:
    return spec.mouser_aliases if provider == "mouser" else spec.digikey_aliases


def _resolve_canonical(
    category_slug: str | None,
    provider: str,
    attributes: dict[str, str],
    mined: dict[str, str],
) -> tuple[dict[str, SpecValue], set[str]]:
    """First alias present wins, attributes before description-mined rows.

    Every alias of a filled key is consumed, whichever source it came
    from, so a losing spelling never reappears as a second Specs row.
    """
    canonical: dict[str, SpecValue] = {}
    consumed: set[str] = set()
    for spec in spec_keys_for(category_slug):
        aliases = _aliases_for(spec, provider)
        from_attributes = [a for a in aliases if a in attributes]
        from_mined = [a for a in aliases if a in mined]
        if not from_attributes and not from_mined:
            continue
        consumed.update(from_attributes)
        consumed.update(from_mined)
        source, present = (
            (attributes, from_attributes) if from_attributes else (mined, from_mined)
        )
        winner = present[0]
        canonical[spec.key] = _to_spec_value(spec, winner, source[winner])
    return canonical, consumed


def _to_spec_value(spec: SpecKey, raw_key: str, raw_value: str) -> SpecValue:
    if spec.unit is None:
        return SpecValue(spec.key, raw_value, None, None, raw_key, raw_value)
    parsed = parse_si(raw_value, unit_hint=spec.unit)
    if parsed is None or parsed.unit != spec.unit:
        # Either unreadable to the parser (`"X7R"` under a unit-bearing key)
        # or readable as the wrong quantity (`"50 V"` under `resistance`,
        # from a mis-aliased payload). Keep the text; leave the sidecar empty
        # rather than invent a number or mix units under one sortable key.
        return SpecValue(spec.key, raw_value, None, spec.unit, raw_key, raw_value)
    return SpecValue(
        spec.key, parsed.display, parsed.value_num, parsed.unit, raw_key, raw_value
    )
