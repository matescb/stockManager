"""What a category says to do with a part's specs, ancestors included.

`part_categories` carries two columns the KiCad document reads
(alembic 0082): `value_template`, which renders the symbol's `Value`
from the part's canonical specs, and `kicad_fields`, the spec keys to
emit as hidden symbol fields. Both are **nullable meaning inherit** — a
template set on *Capacitors* covers *Capacitors / Ceramic* without being
repeated on every leaf — and they inherit independently, so a child may
override the template while still taking its parent's field list.

Kept out of `kicad_library.py` because it is a different question:
that module shapes documents, this one answers "what are this category's
rules" once per page.

**The one query, and when it is skipped.** The page's own categories
arrive already joined onto the part rows, so a flat tree — which is what
every workspace has until somebody nests one — needs no lookup at all.
Only when a category on the page is missing a value AND has a parent is
one query issued for the workspace's active categories, and the walk runs
off that in Python. Never one query per row: the listing's query budget
is pinned by
`tests/test_kicad_api.py::test_listing_does_not_scale_with_part_count`.

**Archived ancestors stop the walk**, the same rule the rest of this
surface applies: an archived category is no category at all here, so it
is absent from the loaded map and inheritance terminates at it rather
than reaching through it. In practice `archive_category` has already
promoted its direct children to root, so this only matters for a tree
that raw SQL left inconsistent.
"""
from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.categories.models import PartCategory
from app.domain.categories.tree import ParentMap, ancestor_ids
from app.domain.custom_fields.models import CustomField
from app.domain.eda.value_template import placeholder_keys

__all__ = [
    "CategoryRules",
    "NO_RULES",
    "custom_fields_by_part",
    "rules_by_category",
    "wanted_custom_field_keys",
]


@dataclass(frozen=True)
class CategoryRules:
    """One category's resolved Value rules.

    `None` is not the same as empty for either field. `None` means the
    column was never set and the walk found no ancestor that set it;
    `kicad_fields=()` means somebody wrote an explicit empty list, which
    is how a child says "emit nothing" against a parent that emits
    something.
    """

    value_template: str | None = None
    kicad_fields: tuple[str, ...] | None = None

    @property
    def complete(self) -> bool:
        """Whether there is nothing left for an ancestor to supply."""
        return self.value_template is not None and self.kicad_fields is not None


NO_RULES = CategoryRules()


def _own_rules(category: PartCategory) -> CategoryRules:
    return CategoryRules(
        value_template=category.value_template,
        kicad_fields=_field_list(category.kicad_fields),
    )


def _field_list(raw: object) -> tuple[str, ...] | None:
    """Coerce the JSONB column to a tuple of keys.

    The API validates the shape on the way in, so anything else here got
    there through raw SQL or a restore. Treating it as unset is the only
    safe reading: a dict would otherwise iterate as its keys and emit
    fields nobody asked for.
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    return tuple(str(item) for item in raw)


def wanted_custom_field_keys(rules: Iterable[CategoryRules]) -> set[str]:
    """Every canonical spec key the given rules read.

    What `kicad_library.py` hands its batched `custom_fields` query, so
    the page fetches the handful of rows it will use rather than the tens
    a provider import writes per part.
    """
    keys: set[str] = set()
    for rule in rules:
        keys |= placeholder_keys(rule.value_template)
        keys.update(rule.kicad_fields or ())
    return keys


def rules_by_category(
    db: Session, *, workspace_id: UUID, categories: Iterable[PartCategory]
) -> dict[UUID, CategoryRules]:
    """Resolved rules for each of `categories`, inheritance applied.

    Costs no query at all unless one of them has something to inherit —
    an unset column AND a parent to look it up from.
    """
    by_id = {category.id: category for category in categories}
    own = {
        category_id: _own_rules(category) for category_id, category in by_id.items()
    }
    if not any(
        not rules.complete and by_id[category_id].parent_id is not None
        for category_id, rules in own.items()
    ):
        return own

    ancestors, parent_map = _workspace_rules(db, workspace_id=workspace_id)
    return {
        category_id: _inherited(category_id, rules, ancestors, parent_map)
        for category_id, rules in own.items()
    }


def _workspace_rules(
    db: Session, *, workspace_id: UUID
) -> tuple[dict[UUID, CategoryRules], ParentMap]:
    """The active categories' own rules plus the map to walk them by.

    Workspace-scoped and active-only, like every other read on this
    surface. The projection is four small columns, and a workspace's
    category count is a hand-curated few dozen — see `tree.py` on why
    this load is deliberately uncapped.
    """
    rows = db.execute(
        select(
            PartCategory.id,
            PartCategory.parent_id,
            PartCategory.value_template,
            PartCategory.kicad_fields,
        )
        .where(PartCategory.workspace_id == workspace_id)
        .where(PartCategory.archived_at.is_(None))
    ).all()
    rules = {
        row.id: CategoryRules(row.value_template, _field_list(row.kicad_fields))
        for row in rows
    }
    return rules, {row.id: row.parent_id for row in rows}


def _inherited(
    category_id: UUID,
    rules: CategoryRules,
    ancestors: dict[UUID, CategoryRules],
    parent_map: ParentMap,
) -> CategoryRules:
    """Fill each unset field from the nearest ancestor that sets it."""
    if rules.complete:
        return rules
    template = rules.value_template
    fields = rules.kicad_fields
    for ancestor_id in ancestor_ids(parent_map, category_id):
        inherited = ancestors.get(ancestor_id)
        if inherited is None:
            # Archived, or gone. It is not part of the tree here, so
            # nothing above it is either.
            break
        if template is None:
            template = inherited.value_template
        if fields is None:
            fields = inherited.kicad_fields
        if template is not None and fields is not None:
            break
    return CategoryRules(template, fields)


def custom_fields_by_part(
    db: Session,
    *,
    workspace_id: UUID,
    part_ids: Sequence[UUID],
    keys: Collection[str],
) -> dict[UUID, dict[str, str]]:
    """`{part_id: {key: value}}` for the keys this page actually reads.

    One query for the whole page — the listing needs a datasheet and a
    handful of specs per row, and a per-row lookup would be exactly the
    N+1 this surface must not have
    (`tests/test_kicad_api.py::test_listing_does_not_scale_with_part_count`).

    `keys` is the union of `datasheet_url` and every canonical spec key
    named by a `value_template` or `kicad_fields` on the page's
    categories, rather than "every custom field these parts have":
    provider imports write tens of rows per part (compliance codes,
    packaging, price breaks) and none of them belong in a symbol.
    """
    if not part_ids or not keys:
        return {}
    rows = db.execute(
        select(CustomField.object_id, CustomField.key, CustomField.value)
        .where(CustomField.workspace_id == workspace_id)
        .where(CustomField.object_type == "part")
        .where(CustomField.object_id.in_(part_ids))
        .where(CustomField.key.in_(sorted(keys)))
        .where(CustomField.archived_at.is_(None))
    ).all()

    out: dict[UUID, dict[str, str]] = {}
    for part_id, key, value in rows:
        if value:
            out.setdefault(part_id, {})[key] = value
    return out
