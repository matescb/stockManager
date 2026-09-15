"""The payloads `/kicad-api/v1` serves — queries, eligibility, shaping.

KiCad's HTTP-library protocol is not the app's `{data, status}`
envelope: it is a small set of raw JSON documents whose exact shape the
KiCad client parses, and in which **every scalar is a string** —
including booleans (`"True"` / `"False"`) and ids. Building them here
rather than in the route keeps `api/routes/kicad.py` down to auth and
dispatch, and makes the shaping testable without an HTTP round-trip.

One document shape, two endpoints
---------------------------------

`list_parts` and `part_detail` emit the SAME document. KiCad 9.0 reads
only `id`, `name` and `description` from a listing row and then fetches
each part individually; KiCad master (10+) keeps a full-shape row as the
part's cached detail and skips that second fetch entirely. Emitting the
full shape in both costs one query per listing and saves the newer
client a round-trip per part, while 9.0 ignores what it doesn't read.

It also means there is no second copy of the shape to drift.

Eligibility
-----------

A part is offered to KiCad iff it is active *and* resolves a
`symbolIdStr`. A part with no symbol would appear in the chooser and
then fail to place, so it is filtered out of the listings and 404s on
detail. Resolution order for both the symbol and the footprint slot:

1. the external reference on `part_eda` — a `LibNick:Entry` naming
   something in the user's own local libraries,
2. a symbol/footprint this workspace hosts, named through
   `kicad_refs` and packaged by phase 6,
3. the part category's default reference,
4. nothing — no symbol means ineligible; no footprint just omits the
   field.

Archived rows do not resolve, anywhere on this surface:

* An archived symbol or footprint is skipped (step 2 joins on
  `archived_at IS NULL`), because phase 6 packages active rows only and
  honouring the link would name an entry that isn't in the generated
  file. The slot falls through to the category default.
* An archived CATEGORY is treated as no category at all. It is absent
  from `categories.json`, so a part filed under one would otherwise be
  reachable through no bucket while its detail still answered 200 using
  that category's defaults — visible nowhere, placeable anyway. Instead
  the part lists under *Uncategorized*, the archived category's
  `default_symbol_ref` / `default_footprint_ref` / `footprint_filters`
  stop applying, and a part that had nothing else becomes ineligible.
  The same rule governs a symbol's or footprint's own category, so an
  entry whose category was archived is referenced (and packaged by
  phase 6) as `PCM_SM_uncategorized`.

Query budget
------------

The category chooser fires a burst of listings, so nothing here may
scale with the number of parts. A listing is three queries whatever the
page size: the joined row query, one batched custom-field lookup and one
batched SPICE-model lookup. `part_detail` runs the same three for its
single row.

A fourth is issued only when a category on the page inherits part of its
Value rules from an ancestor (`kicad_specs.rules_by_category`) — one lookup for the
whole page, never one per row. A flat category tree, which is what every
workspace has until somebody nests one, never pays it.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, select
from sqlalchemy.orm import Session, aliased

from app.core.config import settings
from app.domain.categories.models import PartCategory
from app.domain.categories.tree import tree_paths
from app.domain.eda import kicad_refs
from app.domain.eda.kicad_specs import (
    NO_RULES,
    CategoryRules,
    custom_fields_by_part,
    rules_by_category,
    wanted_custom_field_keys,
)
from app.domain.eda.models import EdaDatafile, EdaFootprint, EdaSymbol, PartEda
from app.domain.eda.value_template import render_value, spec_field_label
from app.domain.parts.models import Part

__all__ = [
    "API_PREFIX",
    "CATEGORIES_TTL_SECONDS",
    "PARTS_TTL_SECONDS",
    "TOKEN_PLACEHOLDER",
    "UNCATEGORIZED_ID",
    "UNCATEGORIZED_NAME",
    "UNCATEGORIZED_DESCRIPTION",
    "root_document",
    "list_categories",
    "category_exists",
    "list_parts",
    "part_detail",
]

# Where `routes/kicad.py` is mounted. Lives here so the `root_url` this
# app tells KiCad to use (`GET /api/eda/kicad-setup`) and the path it
# actually answers on cannot drift apart.
API_PREFIX = "/kicad-api"

# The client's cache lifetimes, written into `.kicad_httplib`. KiCad's
# own code defaults are 30s for parts and 600s for categories; we write
# both explicitly, and lift parts to 60s, because the values in the file
# are what a user reads when asking "why is my edit not showing up" and
# a minute is a better trade for a workspace this size than twice the
# request volume.
CATEGORIES_TTL_SECONDS = 600
PARTS_TTL_SECONDS = 60

# Stands in for the plaintext token in the example config. The UI
# substitutes the real one at mint time — the server never sees a
# recoverable copy.
TOKEN_PLACEHOLDER = "PASTE_YOUR_TOKEN_HERE"

# The id the synthetic "no category" bucket is addressed by. Not a UUID,
# so it can never collide with a real category id.
UNCATEGORIZED_ID = "uncategorized"
UNCATEGORIZED_NAME = "Uncategorized"
UNCATEGORIZED_DESCRIPTION = "Parts without a category"

# The custom field the provider import writes a datasheet URL to — see
# `domain/parts/provider_fields.py`. Its value is either an absolute
# upstream URL or an app-relative path to a downloaded copy.
_DATASHEET_FIELD_KEY = "datasheet_url"

# KiCad hands a datasheet value to the OS URL handler. `http`/`https`
# are the only schemes we will pass on: anything else (`file:`,
# `javascript:`, a bare Windows path) is a request to open something
# local on the engineer's machine, sourced from provider data we don't
# control.
_DATASHEET_SCHEMES = ("http://", "https://")

# KiCad shows a symbol field unless it is told not to. Everything except
# `value` is metadata that would clutter the schematic if drawn.
_HIDDEN = {"visible": "False"}

# Separates the levels of a category's path in `categories.json`. The
# document is `{id, name, description}` and nothing else, so the name is
# the only place nesting can be conveyed — "Capacitors / Ceramic".
_CATEGORY_PATH_SEPARATOR = " / "

# Every field name `_document` may emit for itself, lower-cased. A
# category's `kicad_fields` are matched against this before they are
# emitted: KiCad resolves its mandatory fields case-insensitively, so a
# spec key called `value` would give the symbol two Values, and one
# called `mpn` would give it two MPNs with no way to tell which wins.
_BUILT_IN_FIELD_NAMES = frozenset(
    {
        "footprint",
        "datasheet",
        "value",
        "description",
        "keywords",
        "mpn",
        "manufacturer",
        "ipn",
        "stockmanager",
    }
)

# Aliases at module scope so the filters in `list_parts` and
# `_has_uncategorized_parts` can name the same joined category the
# statement builder does.
_symbol_category = aliased(PartCategory, name="symbol_category")
_footprint_category = aliased(PartCategory, name="footprint_category")
_part_category = aliased(PartCategory, name="part_category")


@dataclass(frozen=True)
class _PartRow:
    """One part with everything its references can resolve from.

    `category` is the part's ACTIVE category, or None — an archived one
    joins as None deliberately (see the module docstring).
    """

    part: Part
    config: PartEda | None
    symbol: EdaSymbol | None
    symbol_category_slug: str | None
    footprint: EdaFootprint | None
    footprint_category_slug: str | None
    category: PartCategory | None


def _bool_str(value: bool) -> str:
    return "True" if value else "False"


def _rows_stmt(workspace_id: UUID) -> Select:
    """Active parts joined to every row the resolution order may read.

    Each join carries its own `workspace_id` equality check alongside
    the FK. The FKs can only point inside the workspace today, so the
    predicate is belt-and-braces — but workspace isolation is enforced
    in code here (ADR-0002) and a join that trusts an FK is exactly the
    kind of hole that convention exists to close.

    Every category join also carries `archived_at IS NULL`, which is
    what makes an archived category behave as no category throughout.
    """
    return (
        select(
            Part,
            PartEda,
            EdaSymbol,
            _symbol_category.library_slug,
            EdaFootprint,
            _footprint_category.library_slug,
            _part_category,
        )
        .outerjoin(
            PartEda,
            and_(
                PartEda.part_id == Part.id,
                PartEda.workspace_id == Part.workspace_id,
            ),
        )
        .outerjoin(
            EdaSymbol,
            and_(
                EdaSymbol.id == PartEda.symbol_id,
                EdaSymbol.workspace_id == Part.workspace_id,
                EdaSymbol.archived_at.is_(None),
            ),
        )
        .outerjoin(
            _symbol_category,
            and_(
                _symbol_category.id == EdaSymbol.category_id,
                _symbol_category.workspace_id == Part.workspace_id,
                _symbol_category.archived_at.is_(None),
            ),
        )
        .outerjoin(
            EdaFootprint,
            and_(
                EdaFootprint.id == PartEda.footprint_id,
                EdaFootprint.workspace_id == Part.workspace_id,
                EdaFootprint.archived_at.is_(None),
            ),
        )
        .outerjoin(
            _footprint_category,
            and_(
                _footprint_category.id == EdaFootprint.category_id,
                _footprint_category.workspace_id == Part.workspace_id,
                _footprint_category.archived_at.is_(None),
            ),
        )
        .outerjoin(
            _part_category,
            and_(
                _part_category.id == Part.category_id,
                _part_category.workspace_id == Part.workspace_id,
                _part_category.archived_at.is_(None),
            ),
        )
        .where(Part.workspace_id == workspace_id)
        .where(Part.archived_at.is_(None))
    )


def _iter_rows(db: Session, stmt: Select) -> Iterator[_PartRow]:
    """Stream `_PartRow`s. A generator so a caller that only needs to
    know whether ONE row qualifies stops building them at the first."""
    for row in db.execute(stmt):
        yield _PartRow(
            part=row[0],
            config=row[1],
            symbol=row[2],
            symbol_category_slug=row[3],
            footprint=row[4],
            footprint_category_slug=row[5],
            category=row[6],
        )


def _symbol_id_str(row: _PartRow) -> str | None:
    """The part's `symbolIdStr`, or None when it has no symbol at all."""
    if row.config is not None and row.config.symbol_ref_external:
        return row.config.symbol_ref_external
    if row.symbol is not None:
        return kicad_refs.symbol_ref(row.symbol, row.symbol_category_slug)
    if row.category is not None and row.category.default_symbol_ref:
        return row.category.default_symbol_ref
    return None


def _footprint_ref(row: _PartRow) -> str | None:
    if row.config is not None and row.config.footprint_ref_external:
        return row.config.footprint_ref_external
    if row.footprint is not None:
        return kicad_refs.footprint_ref(row.footprint, row.footprint_category_slug)
    if row.category is not None and row.category.default_footprint_ref:
        return row.category.default_footprint_ref
    return None


def _footprint_filters(row: _PartRow) -> list[str]:
    if row.config is not None and row.config.footprint_filters:
        return list(row.config.footprint_filters)
    if row.category is not None and row.category.footprint_filters:
        return list(row.category.footprint_filters)
    return []


def _base_url() -> str:
    return settings().APP_BASE_URL.rstrip("/")


def _datasheet_url(value: str | None) -> str | None:
    """A stored `datasheet_url` as an absolute `http(s)` URL KiCad can open.

    The value is app-relative when the provider import downloaded a local
    copy (`/api/parts/assets/…`); KiCad opens it with no notion of our
    origin, so those are made absolute. Anything that is neither relative
    nor `http(s)` is dropped rather than passed to the OS handler.
    """
    if not value:
        return None
    if value.startswith("/"):
        return f"{_base_url()}{value}"
    if value.lower().startswith(_DATASHEET_SCHEMES):
        return value
    return None


def _spice_library_names(
    db: Session, *, workspace_id: UUID, datafile_ids: Sequence[UUID]
) -> dict[UUID, str]:
    """SPICE datafile name per id. Batched, for the same reason.

    Filtered to `kind == "spice"`: `Sim.Library` is emitted as
    `${STOCKMGR_SPICE}/<name>`, so a row pointing at a STEP model would
    name a file that path variable never resolves.
    """
    if not datafile_ids:
        return {}
    rows = db.execute(
        select(EdaDatafile.id, EdaDatafile.name)
        .where(EdaDatafile.workspace_id == workspace_id)
        .where(EdaDatafile.id.in_(datafile_ids))
        .where(EdaDatafile.kind == "spice")
        .where(EdaDatafile.archived_at.is_(None))
    ).all()
    return {datafile_id: name for datafile_id, name in rows}


def _put(fields: dict[str, dict[str, str]], key: str, value: str | None) -> None:
    """Add a hidden symbol field, skipping empty values.

    An empty KiCad field is not nothing — it is a property drawn on
    every instance of the symbol with no content in it.
    """
    if value:
        fields[key] = {"value": value, **_HIDDEN}


def _put_spec_fields(
    fields: dict[str, dict[str, str]],
    *,
    keys: Iterable[str],
    specs: Mapping[str, str],
) -> None:
    """Emit the category's `kicad_fields` as hidden symbol fields.

    `voltage_rating` becomes `Voltage Rating`, which is what makes a
    spec greppable in the schematic editor and present in a BOM export.
    A listed key the part has no value for is omitted rather than
    blanked, same rule as every other field here.

    Called last, and a label that collides with one of ours is skipped:
    KiCad resolves its mandatory fields case-insensitively, so a spec key
    called `value` would give the symbol two Values with no way to say
    which one the schematic draws.
    """
    taken = set(_BUILT_IN_FIELD_NAMES) | {key.lower() for key in fields}
    for key in keys:
        label = spec_field_label(key)
        if label.lower() in taken:
            continue
        taken.add(label.lower())
        _put(fields, label, specs.get(key))


# ---------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------


def root_document() -> dict[str, str]:
    """`GET /v1/` — the endpoint map KiCad probes on connect.

    Both values are empty strings in the reference implementation: the
    client only checks that the keys exist.
    """
    return {"categories": "", "parts": ""}


def list_categories(db: Session, *, workspace_id: UUID) -> list[dict[str, str]]:
    """`GET /v1/categories.json` — every active category, then the
    synthetic *Uncategorized* bucket if any eligible part needs it.

    Categories are listed whether or not they hold eligible parts; an
    empty category in the chooser is a hint that something needs a
    symbol, whereas a missing one looks like data loss.

    **Nesting travels in the name.** The document KiCad parses is
    `{id, name, description}` and nothing else, so a subcategory is named
    by its full path — `Capacitors / Ceramic` — and the rows come back
    depth-first, each child directly under its parent. Without it a
    three-level library is a flat list of leaf names with no way to tell
    *Capacitors / Film* from *Resistors / Film*.
    """
    rows = db.execute(
        select(PartCategory)
        .where(PartCategory.workspace_id == workspace_id)
        .where(PartCategory.archived_at.is_(None))
        .order_by(PartCategory.sort_order, PartCategory.name)
    ).scalars()

    out = [
        {
            "id": str(category.id),
            "name": name,
            "description": category.description or "",
        }
        for category, name in tree_paths(
            list(rows), separator=_CATEGORY_PATH_SEPARATOR
        )
    ]

    if _has_uncategorized_parts(db, workspace_id=workspace_id):
        out.append(
            {
                "id": UNCATEGORIZED_ID,
                "name": UNCATEGORIZED_NAME,
                "description": UNCATEGORIZED_DESCRIPTION,
            }
        )
    return out


def category_exists(db: Session, *, workspace_id: UUID, category_id: UUID) -> bool:
    """Whether the workspace has an active category with this id.

    Archived categories are excluded because `categories.json` doesn't
    list them: a bucket KiCad was never offered must not be addressable.
    A foreign workspace's id answers the same as a nonexistent one.
    """
    return db.execute(
        select(PartCategory.id)
        .where(PartCategory.id == category_id)
        .where(PartCategory.workspace_id == workspace_id)
        .where(PartCategory.archived_at.is_(None))
        .limit(1)
    ).scalar_one_or_none() is not None


def _uncategorized_stmt(workspace_id: UUID) -> Select:
    """Parts with no ACTIVE category — never filed, or filed under one
    that has since been archived."""
    return _rows_stmt(workspace_id).where(_part_category.id.is_(None))


def _has_uncategorized_parts(db: Session, *, workspace_id: UUID) -> bool:
    """Whether any eligible part has no active category.

    Eligibility can't be pushed into SQL without writing the resolution
    order a second time in another language, so this walks the
    uncategorized rows — but `_iter_rows` is a generator and `any`
    short-circuits, so it stops at the first part that resolves rather
    than building the whole set.
    """
    rows = _iter_rows(db, _uncategorized_stmt(workspace_id))
    return any(_symbol_id_str(row) is not None for row in rows)


def _keywords(config: PartEda | None, rendered: str | None) -> str:
    """The part's keywords, with the rendered Value appended.

    KiCad's symbol chooser searches keywords. A part imported from a
    provider is named after the provider's description, so without this
    the 10 kΩ resistor is not findable by typing `10 kΩ` — which is the
    only thing anyone looks for it by.

    Appended even when an explicit `part_eda.value` overrode the render:
    the two are different spellings of the same part and both are worth
    matching on.
    """
    stored = (config.keywords if config else None) or ""
    if not rendered or rendered in stored:
        return stored
    return f"{stored} {rendered}".strip()


def _document(
    row: _PartRow,
    *,
    specs: Mapping[UUID, Mapping[str, str]],
    spice_names: Mapping[UUID, str],
    rules: Mapping[UUID, CategoryRules],
) -> dict[str, Any] | None:
    """The full KiCad part document, or None when the part has no symbol.

    Served by BOTH `list_parts` and `part_detail` — see the module
    docstring. The lookup maps are passed in rather than queried here so
    a listing can batch them.
    """
    symbol_id_str = _symbol_id_str(row)
    if symbol_id_str is None:
        return None

    part = row.part
    config = row.config
    part_specs = specs.get(part.id, {})
    rule = rules.get(row.category.id, NO_RULES) if row.category else NO_RULES
    rendered = render_value(rule.value_template, part_specs, part.mpn)
    keywords = _keywords(config, rendered)

    fields: dict[str, dict[str, str]] = {}
    _put(fields, "footprint", _footprint_ref(row))
    _put(fields, "datasheet", _datasheet_url(part_specs.get(_DATASHEET_FIELD_KEY)))
    # The one field KiCad draws by default — the schematic value. No
    # `visible` key, so the symbol's own default wins.
    #
    # A hand-typed override outranks the category template, which
    # outranks the part name: the template exists because `name` is the
    # provider's marketing copy for most of the library, but a human who
    # typed a value meant it.
    fields["value"] = {
        "value": (config.value if config else None) or rendered or part.name
    }
    _put(fields, "description", part.description)
    _put(fields, "keywords", keywords)
    _put(fields, "MPN", part.mpn)
    _put(fields, "Manufacturer", part.manufacturer)
    _put(fields, "IPN", part.internal_part_number)
    fields["StockManager"] = {
        "value": f"{_base_url()}/parts/{part.id}",
        **_HIDDEN,
    }
    _add_sim_fields(fields, config=config, spice_names=spice_names)
    _put_spec_fields(fields, keys=rule.kicad_fields or (), specs=part_specs)

    document: dict[str, Any] = {
        "id": str(part.id),
        "name": part.name,
        "symbolIdStr": symbol_id_str,
        "description": part.description or "",
        "keywords": keywords,
        "exclude_from_bom": _bool_str(bool(config.exclude_from_bom) if config else False),
        "exclude_from_board": _bool_str(bool(config.exclude_from_board) if config else False),
        # No config means no simulation model, and KiCad treats a symbol
        # that claims to be simulatable but isn't as an error.
        "exclude_from_sim": _bool_str(bool(config.exclude_from_sim) if config else True),
        "fields": fields,
    }
    filters = _footprint_filters(row)
    if filters:
        document["footprint_filters"] = filters
    return document


def _add_sim_fields(
    fields: dict[str, dict[str, str]],
    *,
    config: PartEda | None,
    spice_names: Mapping[UUID, str],
) -> None:
    """Attach the `Sim.*` fields when the part has a usable SPICE model.

    Gated on `exclude_from_sim` as well as on the model being set: the
    flag is how a user turns simulation off for a part whose model is
    wrong or unfinished, and emitting the fields anyway would override
    that from the library side.
    """
    if config is None or config.spice_datafile_id is None or config.exclude_from_sim:
        return
    name = spice_names.get(config.spice_datafile_id)
    if name is None:
        return
    _put(fields, "Sim.Device", config.sim_device)
    _put(fields, "Sim.Pins", config.sim_pins)
    _put(fields, "Sim.Params", config.sim_params)
    _put(fields, "Sim.Library", kicad_refs.spice_path(name))


def _documents(
    db: Session, *, workspace_id: UUID, rows: list[_PartRow]
) -> list[dict[str, Any]]:
    """Build documents for `rows`, batching every per-part lookup.

    The category rules come first because they decide which custom-field
    keys the next query has to ask for.
    """
    rules = rules_by_category(
        db,
        workspace_id=workspace_id,
        categories=[row.category for row in rows if row.category is not None],
    )
    specs = custom_fields_by_part(
        db,
        workspace_id=workspace_id,
        part_ids=[row.part.id for row in rows],
        keys={_DATASHEET_FIELD_KEY} | wanted_custom_field_keys(rules.values()),
    )
    spice_names = _spice_library_names(
        db,
        workspace_id=workspace_id,
        datafile_ids=[
            row.config.spice_datafile_id
            for row in rows
            if row.config is not None and row.config.spice_datafile_id is not None
        ],
    )
    documents = (
        _document(row, specs=specs, spice_names=spice_names, rules=rules)
        for row in rows
    )
    return [document for document in documents if document is not None]


def list_parts(
    db: Session, *, workspace_id: UUID, category_id: UUID | None
) -> list[dict[str, Any]]:
    """`GET /v1/parts/category/{id}.json` — the eligible parts in one bucket.

    `category_id=None` selects the synthetic *Uncategorized* bucket:
    parts with no active category, which includes parts whose category
    has been archived.

    Each row is a complete part document, identical to what
    `part_detail` would return for it — see the module docstring.
    """
    stmt = (
        _uncategorized_stmt(workspace_id)
        if category_id is None
        else _rows_stmt(workspace_id).where(Part.category_id == category_id)
    )
    rows = list(_iter_rows(db, stmt.order_by(Part.name, Part.id)))
    return _documents(db, workspace_id=workspace_id, rows=rows)


def part_detail(
    db: Session, *, workspace_id: UUID, part_id: UUID
) -> dict[str, Any] | None:
    """`GET /v1/parts/{id}.json` — the full symbol definition.

    None for a part that is archived, belongs to another workspace, or
    has no symbol; the route turns all three into the same 404.
    """
    stmt = _rows_stmt(workspace_id).where(Part.id == part_id)
    row = next(_iter_rows(db, stmt), None)
    if row is None:
        return None
    documents = _documents(db, workspace_id=workspace_id, rows=[row])
    return documents[0] if documents else None
