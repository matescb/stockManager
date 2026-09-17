"""Per-category spec columns for the parts list — resolve, validate, fetch, sort.

A category says which canonical spec keys its parts listing shows as
columns (`part_categories.list_columns`, alembic 0083) and which one it
sorts by (`list_sort`). Everything that answers a question about that
lives here, so `api/routes/parts_core.py` and `api/routes/categories.py`
only call in:

  - `effective_schema()` — which spec keys this category HAS, resolved
    through the category tree, plus the stored column/sort choice and
    where it was inherited from.
  - `validated_keys()` / `resolve_request()` — the 422s, in one place, so
    a key the PATCH refuses is a key the listing also refuses.
  - `specs_for_parts()` — the values for a whole page, in ONE statement.
  - `sorted_page()` — the `custom_fields` OUTER JOIN and the keyset seek.

## Why the schema needs a tree walk

`spec_schema.category_slug_for` reads a ` / `-joined NAME PATH, so
"Capacitors / Ceramic" already resolves to `capacitor_ceramic` off the
leaf's own path. What it cannot do is resolve a leaf whose own name
drowns out its parent's: "Passives / Resistors / 0402" flattens to words
that still contain `resistors`, but "Resistors / Precision / High-Z" is
one rename away from not. So the walk retries the path with trailing
segments dropped — leaf path first, then the parent's, up to the root —
and takes the first slug that resolves. A bare root "Capacitors" is
deliberately still unresolved (`CLASS_DEFAULT_SLUG["capacitor"]` is
None: a capacitor with no dielectric named has no schema of its own),
which leaves it with the common keys, which is the honest answer.

## Why the values are one query and the sort is a JOIN

The parts list is the busiest endpoint in the app. Twelve spec columns
over a 200-row page is 2,400 values; a lookup per cell would be 2,400
round-trips, and a lookup per row still 200. `specs_for_parts` is one
`WHERE object_id IN (…) AND key IN (…)`, the same shape as
`_parts_shared.missing_specs_for_parts`.

Sorting cannot be done in Python for the same reason the category filter
cannot (see `categories/tree.py::category_filter_ids`): the page is a
cursor seek over a statement, so ordering the rows that came back
reorders *within* a page and nothing else. It has to be ORDER BY on the
statement, which means the spec has to be JOINed in.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Sequence
from uuid import UUID

from fastapi import status
from sqlalchemy import and_, select
from sqlalchemy.orm import Session, aliased

from app.core.errors import ErrorCodes, raise_http
from app.core.pagination import Cursor, paginate_keyset
from app.domain.categories.models import PartCategory
from app.domain.categories.schemas import MAX_LIST_COLUMNS
from app.domain.categories.service import category_index, get_category
from app.domain.custom_fields.models import CustomField
from app.domain.custom_fields.serialize import value_num_out
from app.domain.parts.models import Part
from app.domain.parts.spec_key import SpecKey
from app.domain.parts.spec_schema import category_slug_for, spec_keys_for

__all__ = [
    "EffectiveSchema",
    "ListSpecRequest",
    "MAX_LIST_COLUMNS",
    "NUMERIC_UNITLESS_KEYS",
    "SPEC_SORT_PREFIX",
    "SpecSort",
    "cursor_scope",
    "effective_schema",
    "is_numeric_key",
    "prospective_schema",
    "resolve_request",
    "serialize_schema",
    "sorted_page",
    "specs_for_parts",
    "validated_keys",
]

# `sort=spec:<key>` — namespaced so the parameter can grow non-spec sorts
# later without the two vocabularies colliding.
SPEC_SORT_PREFIX = "spec:"

# The keys every category carries, whatever its slug.
_COMMON_KEYS: frozenset[str] = frozenset(spec.key for spec in spec_keys_for(None))

# Canonical keys that are numbers despite carrying no unit, and therefore
# right-align in the table like a quantity does.
#
# `SpecKey` has no `numeric` flag: `unit` does that job for everything the
# value parser can read, because `spec_values.parse_si` is only called when
# a unit is declared and `custom_fields.value_num` is only filled when it
# succeeds. These five are counts — `unit=None`, so no `value_num`, so
# they sort as TEXT ("100" before "8"). Listing them here buys the correct
# alignment and nothing more; a numeric sort for them would need a unit on
# the `SpecKey` and a `spec-normalize` re-run, which is a separate change.
NUMERIC_UNITLESS_KEYS: frozenset[str] = frozenset(
    {"channels", "hfe", "pin_count", "positions", "rows"}
)


def is_numeric_key(spec: SpecKey) -> bool:
    """Does this key hold a number? Drives right-alignment, not the sort."""
    return spec.unit is not None or spec.key in NUMERIC_UNITLESS_KEYS


@dataclass(frozen=True)
class SpecSort:
    """A resolved `sort=spec:<key>&dir=…`."""

    key: str
    descending: bool


@dataclass(frozen=True)
class EffectiveSchema:
    """What a category's parts listing can show, and what it does show."""

    #: The `spec_schema_tables` slug the category resolved to, or None —
    #: which is not an error, it means "common keys only".
    slug: str | None
    #: Common keys plus the slug's own, in schema order.
    keys: tuple[SpecKey, ...]
    #: The stored column choice, resolved through the tree. None when
    #: neither this category nor any ancestor has made one.
    list_columns: list[str] | None
    #: The stored default sort, resolved through the tree, independently of
    #: `list_columns` — the same way `value_template` and `kicad_fields`
    #: inherit independently (alembic 0082).
    list_sort: dict[str, str] | None
    #: Which ancestor `list_columns` came from, or None when this category
    #: owns it (or nobody does).
    inherited_from: UUID | None
    #: Same, for `list_sort`.
    sort_inherited_from: UUID | None

    @property
    def key_set(self) -> frozenset[str]:
        return frozenset(spec.key for spec in self.keys)


def effective_schema(
    db: Session, *, ws: Any, category: PartCategory, index: Any | None = None
) -> EffectiveSchema:
    """Resolve one category's spec schema and stored list settings.

    ONE query (`category_index`, workspace-scoped), because all three
    answers are walks over the same `(id, parent_id, name)` map — and no
    query at all when the caller already holds that index. The parts list
    does: `category_filter_ids` needs the same tree to expand the filter,
    and `missing_specs_for_parts` needs it again for the completeness
    badge, so the route loads it once and hands it to all three.
    """
    index = index if index is not None else category_index(db, ws_id=ws.id)
    return _resolved(
        index,
        path=index.paths.get(category.id) or category.name,
        start=category,
        self_id=category.id,
    )


def prospective_schema(
    db: Session,
    *,
    ws: Any,
    name: str,
    parent_id: UUID | None,
    index: Any | None = None,
) -> EffectiveSchema:
    """The schema a category that does not exist yet will have.

    Create has to validate `list_columns` against *something*, and the row
    it would be validated against is the row being created. Without this
    the POST would accept a key the very next PATCH refuses, which is the
    one inconsistency a client cannot work around.

    `list_columns` / `list_sort` are read off the prospective PARENT, and
    the owner is reported even when it is that parent — there is no "self"
    yet for the walk to compare against.
    """
    index = index if index is not None else category_index(db, ws_id=ws.id)
    parent = index.rows_by_id.get(parent_id) if parent_id else None
    parent_path = index.paths.get(parent.id) if parent is not None else None
    return _resolved(
        index,
        path=f"{parent_path} / {name}" if parent_path else name,
        start=parent,
        self_id=None,
    )


def _resolved(
    index, *, path: str, start: PartCategory | None, self_id: UUID | None
) -> EffectiveSchema:
    columns, columns_owner = _inherited(index, start, self_id, "list_columns")
    sort, sort_owner = _inherited(index, start, self_id, "list_sort")
    slug = _slug_for_path(path)
    return EffectiveSchema(
        slug=slug,
        keys=spec_keys_for(slug),
        list_columns=list(columns) if isinstance(columns, list) else None,
        list_sort=sort if isinstance(sort, dict) else None,
        inherited_from=columns_owner,
        sort_inherited_from=sort_owner,
    )


def _slug_for_path(path: str) -> str | None:
    """The spec-schema slug for a name path, walking up on a miss.

    Leaf path first, then with trailing segments dropped.
    `category_slug_for` is word-based over the flattened path, so dropping
    the leaf is what lets an ancestor's noun win when the leaf's own words
    say nothing about what the part is.
    """
    segments = [segment.strip() for segment in path.split("/") if segment.strip()]
    for cut in range(len(segments), 0, -1):
        slug = category_slug_for(" / ".join(segments[:cut]))
        if slug is not None:
            return slug
    return None


def _inherited(
    index, start: PartCategory | None, self_id: UUID | None, attribute: str
) -> tuple[Any, UUID | None]:
    """`(value, owner_id)` for the nearest ancestor-or-`start` that sets it.

    `owner_id` is None when the setter IS `self_id` — "this category owns
    its choice" — and the ancestor's id otherwise, which is what the UI
    renders as "inherited from Resistors".

    Carries a visited-set for the same reason every walk in
    `categories/tree.py` does: `parent_id` survives raw SQL and restored
    backups, and a cycle that hangs the request thread is not worth the
    two lines it saves.
    """
    node = start
    seen: set[UUID] = set()
    while node is not None and node.id not in seen:
        seen.add(node.id)
        value = getattr(node, attribute, None)
        if value is not None:
            return value, (None if node.id == self_id else node.id)
        node = index.rows_by_id.get(node.parent_id) if node.parent_id else None
    return None, None


def serialize_schema(schema: EffectiveSchema) -> dict:
    """The `GET /api/categories/{id}/spec-schema` payload."""
    return {
        "slug": schema.slug,
        "keys": [
            {
                "key": spec.key,
                "label": spec.label,
                "unit": spec.unit,
                "mandatory": spec.mandatory,
                # A display hint (right-align), not a promise about the
                # sort — see `NUMERIC_UNITLESS_KEYS`.
                "numeric": is_numeric_key(spec),
                # True for the keys every category carries, so the picker
                # can group "Resistance, Tolerance…" apart from
                # "Package, Mounting…" without a second request.
                "common": spec.key in _COMMON_KEYS,
            }
            for spec in schema.keys
        ],
        "list_columns": schema.list_columns,
        "list_sort": schema.list_sort,
        "inherited_from": str(schema.inherited_from) if schema.inherited_from else None,
        "sort_inherited_from": (
            str(schema.sort_inherited_from) if schema.sort_inherited_from else None
        ),
    }


def validated_keys(keys: Sequence[str], schema: EffectiveSchema) -> list[str]:
    """Refuse a key this category has no schema for, or more than the cap.

    The 422 names the offending key in `key`, because "unknown spec key"
    with no key in it is a dead end for a client that sent twelve.
    """
    if len(keys) > MAX_LIST_COLUMNS:
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=ErrorCodes.CATEGORY_TOO_MANY_SPEC_COLUMNS,
            message=f"at most {MAX_LIST_COLUMNS} spec columns, got {len(keys)}",
            max_columns=MAX_LIST_COLUMNS,
        )
    allowed = schema.key_set
    for key in keys:
        if key not in allowed:
            raise_http(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                code=ErrorCodes.CATEGORY_UNKNOWN_SPEC_KEY,
                message=(
                    f'"{key}" is not a spec key for this category '
                    f'(schema: {schema.slug or "common"})'
                ),
                key=key,
            )
    return list(keys)


@dataclass(frozen=True)
class ListSpecRequest:
    """What one `GET /api/parts` call asked for, validated."""

    columns: tuple[str, ...]
    sort: SpecSort | None
    #: The keyset cursor's `scope` for this listing — `None` when it is not
    #: spec-sorted. See `cursor_scope`.
    cursor_scope: str | None = None


def cursor_scope(
    sort: SpecSort, *, category_id: UUID, include_descendants: bool
) -> str:
    """The discriminator signed into a spec-sorted page's cursor.

    The seek values cannot identify their own sort: `10000` is a legal
    `resistance` and a legal `voltage_rating`, and the same pair read
    backwards is a legal descending seek. So everything that decides the
    ORDER BY goes in here, plus the category filter — a forward seek under
    a *narrowed* filter silently skips whatever sorted before the seek
    position, which is the same trap `category_filter_ids` documents for
    the unsorted path.

    Not included: `q`, `mpn`, `archived`. They narrow the row set without
    touching the ordering, so a cursor across a change to one of them
    behaves exactly as it always has on the unsorted path — a shorter
    walk, never a wrong one. Adding them here would 400 the common case of
    typing in the search box mid-scroll.
    """
    return ":".join((
        "spec",
        sort.key,
        "desc" if sort.descending else "asc",
        str(category_id),
        "tree" if include_descendants else "exact",
    ))


def resolve_request(
    db: Session,
    *,
    ws: Any,
    category_id: UUID | None,
    columns: str | None,
    sort: str | None,
    direction: str,
    include_descendants: bool = True,
    index: Any | None = None,
) -> ListSpecRequest:
    """Validate the parts list's `spec_columns` / `sort` / `dir` params.

    Off entirely without `category_id`: the key vocabulary IS the
    category's, so there is nothing to validate a key against and nothing
    sensible to show. Both params are ignored rather than refused in that
    case — they are a view setting, and a client that drops the category
    filter while they are still on the URL should get the unfiltered list,
    not an error.

    With no `sort`, the category's stored `list_sort` applies (resolved
    through the tree). That is the only way `GET /api/parts` behaves
    differently from before this feature, and only for a category somebody
    has configured.

    Resolving the schema costs a load of the workspace's categories, so a
    request that cannot possibly need one does not pay for it: nothing
    asked for columns, nothing asked for a sort, this category has no
    stored sort of its own, and it has no parent to inherit one from. That
    is the shape of every category-filtered listing until somebody
    configures a category, which is what keeps this feature free for
    workspaces that do not use it.
    """
    if category_id is None:
        return ListSpecRequest((), None)

    category = _category(db, ws=ws, category_id=category_id, index=index)
    if (
        not columns
        and sort is None
        and category.list_sort is None
        and category.parent_id is None
    ):
        return ListSpecRequest((), None)
    schema = effective_schema(db, ws=ws, category=category, index=index)
    requested = validated_keys(_split_columns(columns), schema)

    if sort is None:
        resolved = _default_sort(schema)
    elif not sort.startswith(SPEC_SORT_PREFIX):
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=ErrorCodes.CATEGORY_UNKNOWN_SPEC_KEY,
            message=f'sort must be "{SPEC_SORT_PREFIX}<spec key>"',
            key=sort,
        )
    else:
        key = sort[len(SPEC_SORT_PREFIX):]
        validated_keys([key], schema)
        resolved = SpecSort(key, direction == "desc")

    return ListSpecRequest(
        tuple(requested),
        resolved,
        None
        if resolved is None
        else cursor_scope(
            resolved,
            category_id=category_id,
            include_descendants=include_descendants,
        ),
    )


def _category(
    db: Session, *, ws: Any, category_id: UUID, index: Any | None
) -> PartCategory:
    """The category, from a shared index when there is one.

    The index is already workspace-scoped, so a lookup in it gives exactly
    the 404-on-foreign-or-missing that `get_category` gives — the
    ADR-0002 rule that a foreign UUID must be indistinguishable from a
    missing one. Using it saves the route a second point-select of a row
    `category_filter_ids` has already resolved.
    """
    if index is None:
        return get_category(db, ws=ws, category_id=category_id)
    row = index.rows_by_id.get(category_id)
    if row is None:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCodes.CATEGORY_NOT_FOUND,
            message="category not found",
        )
    return row


def _split_columns(raw: str | None) -> list[str]:
    """`"resistance, tolerance"` -> `["resistance", "tolerance"]`.

    Repeats are dropped, keeping the caller's order — the same rule
    `categories/schemas.py::_deduped` applies to the stored list, so a
    round-trip through the API cannot change the column count.
    """
    if not raw:
        return []
    seen = [part.strip() for part in raw.split(",") if part.strip()]
    return list(dict.fromkeys(seen))


def _default_sort(schema: EffectiveSchema) -> SpecSort | None:
    """The category's `list_sort`, if it still names a key it has.

    A stored sort can outlive its schema — a category renamed from
    "Ceramic capacitors" to "Capacitors" loses `dielectric`. Dropping the
    sort is better than a 422 on every listing: the user did not ask for
    the sort on this request, and an unsortable saved default should not
    make the category unreadable.
    """
    stored = schema.list_sort
    if not stored:
        return None
    key = stored.get("key")
    if not isinstance(key, str) or key not in schema.key_set:
        return None
    return SpecSort(key, stored.get("dir") == "desc")


def specs_for_parts(
    db: Session, *, ws_id: UUID, part_ids: Sequence[UUID], keys: Iterable[str]
) -> dict[UUID, dict[str, dict[str, str | None]]]:
    """`part_id -> {key: {value, value_num}}` for a whole page, in ONE query.

    Every requested key is present on every part, with both values null
    when the part has no row for it. That is the same "looked, found none"
    contract `provider_links: []` carries on these rows: a missing key
    would be indistinguishable from a key the request never asked for, and
    the table would have no way to tell a blank cell from a dropped column.

    Rows of any `source` count — a value the user typed is still the value
    (the same rule `missing_specs_for_parts` applies). Archived rows do
    not: they are retired data.
    """
    wanted = list(dict.fromkeys(keys))
    if not part_ids or not wanted:
        return {}
    out: dict[UUID, dict[str, dict[str, str | None]]] = {
        part_id: {key: {"value": None, "value_num": None} for key in wanted}
        for part_id in part_ids
    }
    rows = db.execute(
        select(
            CustomField.object_id,
            CustomField.key,
            CustomField.value,
            CustomField.value_num,
        )
        .where(CustomField.workspace_id == ws_id)
        .where(CustomField.object_type == "part")
        .where(CustomField.object_id.in_(part_ids))
        .where(CustomField.key.in_(wanted))
        .where(CustomField.archived_at.is_(None))
    ).all()
    for object_id, key, value, value_num in rows:
        bucket = out.get(object_id)
        if bucket is None or key not in bucket:
            continue
        bucket[key] = {"value": value, "value_num": value_num_out(value_num)}
    return out


def sorted_page(
    db: Session,
    stmt,
    *,
    ws_id: UUID,
    sort: SpecSort,
    cursor: Cursor | None,
    limit: int,
    scope: str | None = None,
) -> tuple[list[Part], str | None]:
    """A page of parts ordered by one spec key, newest seek position out.

    `ORDER BY value_num <dir> NULLS LAST, value <dir> NULLS LAST, id ASC`.
    Two expressions for one column on purpose:

      - `value_num` first, so `10 kΩ` (10000) sorts before `100 kΩ`
        (100000) instead of after it the way the display strings would;
      - `value` second, so a key with no unit (`package`, `dielectric`)
        still sorts alphabetically rather than collapsing into a single
        undifferentiated NULL block — every one of its rows has a NULL
        `value_num`.

    NULLS LAST on both: a part with no such spec belongs at the end of the
    list whichever way it is sorted, not interleaved at one end.

    The JOIN is an OUTER one, so a part *without* the spec is still on the
    page. `uq_cf_unique` makes the match single-valued, so it adds no rows.

    **The ordering is NOT delivered by an index, and cannot be.** Measured
    plan, both at a typical workspace size and at fifty times one:

        Limit -> Sort (top-N heapsort)
                   -> Hash Right Join
                        -> Seq Scan on custom_fields
                        -> Seq Scan on parts

    | workspace | plan | exec |
    |---|---|---|
    | 400 parts, 400 spec rows | as above | 0.35 ms |
    | 20,400 parts, 20,400 spec rows | as above | 18.5 ms |

    `ix_custom_fields_ws_key_value_num` is never used, and adding an index
    would not remove that `Sort`. The reason is the OUTER join, not the
    index's shape: a part with no `custom_fields` row for the key has no
    row to index, and it still has to sort into the NULLS-LAST tail — so
    the full ordering only exists *after* the join, which is where the
    sort node is. The index could at best order the non-NULL prefix, and
    it cannot even do the lookup without a heap fetch per row because it
    carries neither `object_id` nor `archived_at`.

    So the cost is a scan and a top-N sort of the workspace's parts per
    page, and that is **accepted deliberately**: 0.35 ms at the size any
    real workspace here is, ~18 ms at a size none has reached. If a
    category ever holds tens of thousands of parts, the next step is a
    composite `(workspace_id, key, value_num, object_id) WHERE archived_at
    IS NULL` — which makes the join side index-only and lets the non-NULL
    prefix be read in order — as a new migration, plus a query shaped so
    that prefix drives and the no-row parts are appended. Not worth a
    migration on measured data. Pinned by
    `tests/test_spec_columns.py::test_the_spec_sort_plan_is_a_scan_and_a_sort`.
    """
    spec = aliased(CustomField)
    stmt = stmt.outerjoin(
        spec,
        and_(
            spec.workspace_id == ws_id,
            spec.object_type == "part",
            spec.object_id == Part.id,
            spec.key == sort.key,
            spec.archived_at.is_(None),
        ),
    )
    return paginate_keyset(
        db,
        stmt,
        sort_exprs=(spec.value_num, spec.value),
        id_col=Part.id,
        cursor=cursor,
        limit=limit,
        asc=not sort.descending,
        # `value_num` is `NUMERIC(36,18)`; handing Postgres the cursor's
        # string would be a `numeric > text` type error.
        decoders=(Decimal, str),
        # Signed into the cursor, so a seek position minted under a
        # different key, direction or category filter is a 400 instead of
        # being honoured against an ordering it does not describe.
        scope=scope,
    )

