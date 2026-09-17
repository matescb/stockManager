"""Pull the part of a provider value that one canonical key is about.

Most canonical keys take the whole value: `Resistance` is the resistance,
and `spec_values.parse_si` does the rest. Three do not, and each is a
shape DigiKey and Mouser actually send:

* **`Size / Dimension` is two numbers.** `0.126" L x 0.063" W (3.20mm x
  1.60mm)` is the length AND the width, so one raw key feeds two
  canonical keys. It is the only place in the schema where that happens,
  and `spec_schema.normalise` supports it because `_resolve_canonical`
  matches aliases against the payload rather than against what an earlier
  key already consumed — see ADR-0034.
* **Dimensions arrive in inches with a metric equivalent in
  parentheses.** `parse_si` has no inch entry and is not getting one: a
  unit *conversion* is a different thing from an SI prefix, and adding
  one would mean the parser silently rescaling numbers. Preferring the
  metric figure the vendor already supplied gets the same answer without
  that.
* **`Ratings` is a list, of which one entry is a spec.** It carries
  `AEC-Q200`, `Automotive`, `Moisture Resistant` and much else besides.
  Only the AEC-Q qualification is a fact about the part worth a sortable
  column; the rest is prose, which is why the key sat on the junk
  denylist until this module gave it something to mean.

Two contracts every extractor keeps, both pinned by
`tests/test_spec_extract.py`:

* **``None`` means "this value says nothing about that key".**
  `normalise()` records the alias as dropped rather than keeping the
  prose verbatim, which is what stops a non-automotive `Ratings` value
  from reappearing on the Specs tab.
* **Idempotent on its own output.** The `spec-normalize` backfill
  re-reads its own rows through `spec_schema.canonical_value`, which runs
  the extractor a second time. A second pass that moved the value would
  make the job rewrite every row on every run.

Pure functions — no DB, no I/O, no config. The keys that name them are in
`spec_schema_tables_more.py`, which is where every `SpecKey.extract` in
the schema lives; the call site is `spec_schema._to_spec_value`.
"""
from __future__ import annotations

import re
from collections.abc import Callable

__all__ = ["EXTRACTORS", "extract_for"]

# A parenthesised metric equivalent: a group carrying a digit and a `mm`
# or `cm` token. Anchored on the unit rather than on the parentheses so
# `0402 (1005 Metric)` — a package-code note, not a length — is left
# alone. `Metric` does not match `mm|cm` followed by a word boundary.
_METRIC_PAREN_RE = re.compile(r"\(([^()]*\d[^()]*?(?:mm|cm)\b[^()]*)\)", re.IGNORECASE)

# `3.20mm x 1.60mm`. Whitespace on BOTH sides is required: splitting on a
# bare `x` cuts "Max" in half and leaves "3.2 mm Ma", which parses as
# nothing at all.
_DIMENSION_SPLIT_RE = re.compile(r"\s+[x×]\s+", re.IGNORECASE)

# `AEC-Q200`, `AEC-Q100`, and the two spacings vendors use for them.
_AEC_RE = re.compile(r"\bAEC[\s-]?Q\s?(\d{3})\b", re.IGNORECASE)


def metric(value: str) -> str | None:
    """The metric equivalent the vendor put in parentheses, else the text.

    `0.087" (2.20mm)` -> `2.20mm`; `2.2 mm` -> `2.2 mm`.
    """
    text = (value or "").strip()
    if not text:
        return None
    found = _METRIC_PAREN_RE.findall(text)
    return found[-1].strip() if found else text


def dimension_first(value: str) -> str | None:
    """The first of a `L x W` pair, metric where the vendor gave one."""
    return _dimension(value, 0)


def dimension_last(value: str) -> str | None:
    """The last of a `L x W` pair.

    "Last" rather than "second" on purpose: Mouser sends `Width` as its
    own key with a single value, and a round part's `Size / Dimension` is
    one diameter. Both have to survive as a width.
    """
    return _dimension(value, -1)


def aec_qualification(value: str) -> str | None:
    """`AEC-Q200` out of a `Ratings` list, or ``None`` for ordinary prose."""
    match = _AEC_RE.search(value or "")
    return f"AEC-Q{match.group(1)}" if match else None


def _dimension(value: str, index: int) -> str | None:
    text = metric(value)
    if text is None:
        return None
    parts = [p.strip() for p in _DIMENSION_SPLIT_RE.split(text) if p.strip()]
    return parts[index] if parts else None


EXTRACTORS: dict[str, Callable[[str], str | None]] = {
    "metric": metric,
    "dimension_first": dimension_first,
    "dimension_last": dimension_last,
    "aec_qualification": aec_qualification,
}


def extract_for(name: str, value: str) -> str | None:
    """Run the named extractor. Unknown name -> `KeyError`.

    The name comes from a `SpecKey` in `spec_schema_tables_more.py`, never
    from user input or a provider payload, so a typo is a programming
    error and must fail at the call rather than quietly drop a canonical
    key for every part. `test_spec_schema.py` catches it before an import
    does.
    """
    return EXTRACTORS[name](value)
