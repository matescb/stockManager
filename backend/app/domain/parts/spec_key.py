"""The row type both spec-schema table modules are written in.

Its own module only so `spec_schema_tables.py` and
`spec_schema_tables_more.py` can each import it without importing each
other. Everything else about the schema — the keys, the aliases, the junk
denylist — lives in those two; the logic that reads them is
`spec_schema.py`. See ADR-0034.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SpecKey:
    """One canonical spec on one category.

    `unit` is the SI base-unit symbol used to parse and render the value
    (`"Ω"`, `"F"`, `"W"`, `"%"`, `"ppm/°C"`). ``None`` means the value is
    not a quantity — a package code, a dielectric name, a colour, a bare
    count — and is kept verbatim with no `value_num` sidecar.

    `extract` names a transform in `spec_extract.EXTRACTORS`, applied to
    the raw value before it is parsed. It is what lets one upstream key
    feed two canonical keys (`Size / Dimension` is a length AND a width)
    and what lets a key take one fact out of a prose list (`Ratings` ->
    `AEC-Q200`). A transform that returns ``None`` leaves the key unfilled
    and the alias recorded as dropped, so the prose is not kept verbatim.
    """

    key: str
    unit: str | None
    label: str
    mandatory: bool
    digikey_aliases: tuple[str, ...] = field(default=())
    mouser_aliases: tuple[str, ...] = field(default=())
    extract: str | None = None
