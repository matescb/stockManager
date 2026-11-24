"""Lifecycle and compliance as structured fields (SM-004).

A derived READ model. Nothing here writes: the values are the catalog
rows the provider refresh already stores (`spec_reconcile` is the only
writer), or — for `catalog_lookup` — the provider record itself, which is
never stored. Both paths go through `_view`, so an agent reads one shape
whichever tool answered.

Where a value comes from
------------------------
Each `custom_fields` row is attributed to one SOURCE:

* `manual` — a `manual` or `override` row: the value is the user's.
* a provider name — a `provider` row. A `"{provider}:"`-prefixed key is
  that secondary's (ADR-0031); a bare key is whoever stamped
  `custom_fields.provider`, else the workspace's primary (rows predating
  the stamp were all written by the primary).

Sources rank `manual` first (the user curated it on purpose), then
`spec_schema.PROVIDER_PRECEDENCE` (DigiKey > Mouser). The best source
answers; every other source that supplied a value is listed under
`alternates`, so "obsolete at Mouser, active at DigiKey" stays visible.

`refreshed_at` is the later of the rows' `updated_at` and the provider
link's `last_refresh_at`. A row's `updated_at` moves only when its value
changes, so the link's timestamp is what says "re-read on that date".

Status vocabulary — `normalise_status`, first rule that matches wins,
on the case-folded raw string (kept as `raw`):

=================================================  ==============
raw (DigiKey `ProductStatus` / Mouser lifecycle)   status
=================================================  ==============
"Not Recommended for New Designs", "Not For New    nrnd
Designs", "NRND"
"Last Time Buy", "End of Life" (EOL announced,     last-time-buy
the last-time-buy window is open)
"Obsolete"                                         obsolete
"Discontinued at Digi-Key", "Discontinued"         discontinued
"Preliminary", "Pre-Release", "Coming Soon"        preliminary
"Active", "Production", "New Product",             active
"New at Mouser"
anything else (e.g. "Factory Special Order"),      unknown
or no value
=================================================  ==============

With no status string, a `Discontinued` flag makes the status
`discontinued` and an `End of life` flag makes it `last-time-buy`.

ECCN, HTS and MSL are on ADR-0034's junk list, so the refresh retires
them instead of storing them: for a stored part they are `unknown`
unless entered by hand. `catalog_lookup` reads them from the live record.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from app.domain.custom_fields.models import CustomField
from app.domain.parts.models import PartProviderLink
from app.domain.parts.provider_fields import KNOWN_PROVIDER_NAMES

__all__ = [
    "LIFECYCLE_STATUSES",
    "UNKNOWN",
    "for_parts",
    "from_record",
    "normalise_status",
]

UNKNOWN = "unknown"
MANUAL = "manual"
LIFECYCLE_STATUSES: tuple[str, ...] = (
    "active", "nrnd", "last-time-buy", "obsolete", "discontinued", "preliminary", UNKNOWN,
)

# Ranking of providers, best first. Mirrors `spec_schema.PROVIDER_PRECEDENCE`;
# not imported from there because `spec_schema` pulls the whole schema
# tables in for what is a two-name tuple (a test pins the two equal).
PROVIDER_RANK: tuple[str, ...] = ("digikey", "mouser")

_STATUS_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"not (?:recommended )?for new designs?|\bnrnd\b"), "nrnd"),
    (re.compile(r"last[- ]time[- ]buy|\bltb\b|end[- ]of[- ]life|\beol\b"), "last-time-buy"),
    (re.compile(r"obsolete"), "obsolete"),
    (re.compile(r"discontinued"), "discontinued"),
    (re.compile(r"preliminary|pre-?release|coming soon"), "preliminary"),
    (re.compile(r"\bactive\b|\bproduction\b|\bnew product\b|\bnew at mouser\b"), "active"),
)

# Field -> the row keys that answer it, best first, compared case-folded
# and without a provider prefix. The provider spellings are the ones the
# adapters emit (`digikey.py::_record_from_product`, `mouser.py`).
LIFECYCLE_KEYS: dict[str, tuple[str, ...]] = {
    "status": ("lifecycle", "lifecycle status"),
    "discontinued": ("discontinued",),
    "end_of_life": ("end of life",),
}
COMPLIANCE_KEYS: dict[str, tuple[str, ...]] = {
    "rohs": ("rohs", "rohs status"),
    "reach": ("reach", "reach svhc"),
    "eccn": ("eccn",),
    "hts": ("hts code", "ushts", "hts"),
    "msl": ("msl", "moisture sensitivity level"),
}
_FLAGS = ("discontinued", "end_of_life")
_KEY_FIELD: dict[str, tuple[str, str, int]] = {
    alias: (block, field, rank)
    for block, table in (("lifecycle", LIFECYCLE_KEYS), ("compliance", COMPLIANCE_KEYS))
    for field, aliases in table.items()
    for rank, alias in enumerate(aliases)
}
_FALSE = {"no", "false", "0", "n"}

# One source's answer: block -> field -> (value, alias rank, updated_at).
_Snapshot = dict[str, dict[str, tuple[str, int, datetime | None]]]


def normalise_status(raw: str | None) -> str:
    """Map a provider's lifecycle string onto `LIFECYCLE_STATUSES`."""
    text = (raw or "").strip().casefold()
    if not text:
        return UNKNOWN
    for pattern, status in _STATUS_RULES:
        if pattern.search(text):
            return status
    return UNKNOWN


# ---------------------------------------------------------------------------
# stored rows (get_part, export_parts)
# ---------------------------------------------------------------------------
def for_parts(db, ws, part_ids: Sequence[UUID]) -> dict[UUID, dict[str, Any]]:
    """`{part_id: {"lifecycle": …, "compliance": …}}` — two queries total.

    Every part in *part_ids* gets an entry, `unknown` throughout when
    nothing answered. Both queries filter by `ws.id`.
    """
    ws_id: UUID = ws.id
    primary = (ws.parts_provider or "").strip().lower() or None
    ids = list(part_ids)
    if not ids:
        return {}
    wanted = _stored_key_spellings()
    rows = db.execute(
        select(CustomField)
        .where(CustomField.workspace_id == ws_id)
        .where(CustomField.object_type == "part")
        .where(CustomField.object_id.in_(ids))
        .where(CustomField.archived_at.is_(None))
        .where(func.lower(CustomField.key).in_(wanted))
    ).scalars()
    snaps: dict[UUID, dict[str, _Snapshot]] = {pid: {} for pid in ids}
    for row in rows:
        source, bare = _attribute(row, primary)
        _note(snaps[row.object_id], source, bare, row.value, row.updated_at)

    links: dict[UUID, dict[str, datetime]] = {pid: {} for pid in ids}
    for part_id, provider, stamp in db.execute(
        select(PartProviderLink.part_id, PartProviderLink.provider,
               PartProviderLink.last_refresh_at)
        .where(PartProviderLink.workspace_id == ws_id)
        .where(PartProviderLink.part_id.in_(ids))
        .where(PartProviderLink.archived_at.is_(None))
    ).all():
        if stamp is not None:
            links[part_id][provider] = stamp
    return {pid: _view(snaps[pid], links[pid], missing_flag_is_false=False) for pid in ids}


def _stored_key_spellings() -> list[str]:
    bare = list(_KEY_FIELD)
    return bare + [f"{name}:{key}" for name in KNOWN_PROVIDER_NAMES for key in bare]


def _attribute(row: Any, primary: str | None) -> tuple[str, str]:
    """(source, key without a provider prefix) for one row."""
    name, sep, rest = row.key.partition(":")
    namespaced = bool(sep) and name in KNOWN_PROVIDER_NAMES
    bare = rest if namespaced else row.key
    if row.source != "provider":
        return MANUAL, bare
    if namespaced:
        return name, bare
    return (row.provider or primary or "provider"), bare


def _note(snap: dict[str, _Snapshot], source: str, key: str, value: Any,
          updated_at: datetime | None) -> None:
    hit = _KEY_FIELD.get(key.strip().casefold())
    text = str(value).strip() if value is not None else ""
    if hit is None or not text:
        return
    block, field, rank = hit
    fields = snap.setdefault(source, {}).setdefault(block, {})
    held = fields.get(field)
    if held is None or rank < held[1]:
        fields[field] = (text, rank, updated_at)


# ---------------------------------------------------------------------------
# a provider record (catalog_lookup)
# ---------------------------------------------------------------------------
def from_record(
    specs: Mapping[str, Any], *, source: str, retrieved_at: datetime | None
) -> dict[str, Any]:
    """The same two blocks from one provider record's `specs` (not stored).

    A provider record is complete: a flag it did not send is `false`,
    not `unknown` (the adapters emit `Discontinued: yes` only when set).
    """
    snap: dict[str, _Snapshot] = {}
    for key, value in specs.items():
        if key:
            _note(snap, source, str(key), value, retrieved_at)
    out = _view(snap, {}, missing_flag_is_false=True)
    life = out["lifecycle"]
    if specs.get("Suggested replacement"):
        life["suggested_replacement"] = specs["Suggested replacement"]
    if specs.get("NCNR"):
        life["ncnr"] = True
    return out


# ---------------------------------------------------------------------------
# shaping
# ---------------------------------------------------------------------------
def _view(snaps: dict[str, _Snapshot], links: Mapping[str, datetime], *,
          missing_flag_is_false: bool) -> dict[str, Any]:
    ranked = sorted(snaps, key=_rank)
    return {
        "lifecycle": _block(
            "lifecycle", ranked, snaps, links,
            lambda src, f: _lifecycle_view(src, f, missing_flag_is_false or src != MANUAL),
            _unknown_lifecycle,
        ),
        "compliance": _block(
            "compliance", ranked, snaps, links, _compliance_view, _unknown_compliance,
        ),
    }


def _block(block, ranked, snaps, links, shape, empty) -> dict[str, Any]:
    sources = [s for s in ranked if snaps[s].get(block)]
    if not sources:
        return empty()
    views = []
    for src in sources:
        fields = snaps[src][block]
        view = shape(src, fields)
        view["source"] = src
        view["refreshed_at"] = _iso(_latest(fields, links.get(src)))
        views.append(view)
    main = views[0]
    if len(views) > 1:
        main["alternates"] = [_alternate(block, v) for v in views[1:]]
    return main


def _lifecycle_view(source: str, fields, flags_default_false: bool) -> dict[str, Any]:
    raw = fields.get("status", (None,))[0]
    flags = {
        name: _flag(fields[name][0]) if name in fields
        else (False if flags_default_false else UNKNOWN)
        for name in _FLAGS
    }
    status = normalise_status(raw)
    if status == UNKNOWN and flags["discontinued"] is True:
        status = "discontinued"
    elif status == UNKNOWN and flags["end_of_life"] is True:
        status = "last-time-buy"
    out: dict[str, Any] = {"status": status}
    if raw:
        out["raw"] = raw
    out.update(flags)
    return out


def _compliance_view(source: str, fields) -> dict[str, Any]:
    return {name: fields[name][0] if name in fields else UNKNOWN for name in COMPLIANCE_KEYS}


def _alternate(block: str, view: dict[str, Any]) -> dict[str, Any]:
    if block == "lifecycle":
        keys = ("source", "status", "raw", "discontinued", "end_of_life", "refreshed_at")
        return {k: view[k] for k in keys if k in view}
    return {k: v for k, v in view.items() if v != UNKNOWN}


def _unknown_lifecycle() -> dict[str, Any]:
    return {"status": UNKNOWN, "discontinued": UNKNOWN, "end_of_life": UNKNOWN,
            "source": UNKNOWN, "refreshed_at": None}


def _unknown_compliance() -> dict[str, Any]:
    return {**{name: UNKNOWN for name in COMPLIANCE_KEYS},
            "source": UNKNOWN, "refreshed_at": None}


def _flag(value: str) -> bool:
    return value.strip().casefold() not in _FALSE


def _rank(source: str) -> tuple[int, str]:
    if source == MANUAL:
        return (0, source)
    if source in PROVIDER_RANK:
        return (1 + PROVIDER_RANK.index(source), source)
    return (1 + len(PROVIDER_RANK), source)


def _latest(fields: Mapping[str, tuple[str, int, datetime | None]],
            link: datetime | None) -> datetime | None:
    stamps: Iterable[datetime | None] = [f[2] for f in fields.values()] + [link]
    present = [s for s in stamps if s is not None]
    return max(present) if present else None


def _iso(stamp: Any) -> str | None:
    return stamp.isoformat() if hasattr(stamp, "isoformat") else stamp
