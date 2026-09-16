"""What a part's `name` is supposed to say, and what it currently says.

`parts.name` is the **canonical identity** of the component, never
project prose. Two rules cover the catalogue:

* a part whose category (or its nearest ancestor) carries a
  `value_template` is named by that template behind the category's
  class letter — `R 10 kΩ 1% 0603`, `C 1 µF 50 V X7R 0805`,
  `L 22 µH 5.3 A 1210`;
* everything else is named by its MPN. The manufacturer stays in its own
  column and the provider's marketing copy stays in `description`.

Project-specific role text ("— Servo integrator + bias monitor") is a
property of the BOM line, not of the part, and lives on the project
entry.

This module answers both halves of that: `canonical_name` computes the
name the convention *wants*, and `classify_name` / `propose_rename` read
the name a part *has* so the `part-rename` job can report and fix the
gap. Nothing here renames anything — creation applies it
(`services/provider_import.py`), and the job applies it in bulk.

**Why the template can come back empty.** Provider imports write specs
under the vendor's own key names (`Resistance`, `Voltage - Rated`);
templates read canonical keys (`resistance`, `voltage_rating`). Until
the spec normalisation backfill re-keys those rows, `canonical_name`
returns `None` for most parts and every caller falls back to the MPN.
That is the designed behaviour, not a degraded mode. A template renders
a name only when EVERY one of its placeholders resolves: a partial
render is what would name every half-specified resistor in a workspace
`R 0603`, and a part number beats that.

**Names are not unique.** `parts.name` carries a plain index and a
trigram index for search, but no UNIQUE constraint — `uq_parts_ws_mpn`
is the identity constraint — and every downstream consumer keys on the
part id, KiCad included (`domain/eda/kicad_refs.py`). Two
`R 10 kΩ 1% 0603` rows from different manufacturers are a duplicate to
resolve in the catalogue, not an error this module has to prevent.

**What a rename may not do is lose text.** `propose_rename` says where
the old name survives (`RenameProposal.preserved_in`) rather than
assuming it does, because the assumption was wrong twice: `description`
looks self-preserving and is provider-owned, so a refresh rewrites it;
and the `alias` slot a rename wants may already be occupied, which only
the caller can see. The job resolves the second case and reports both.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.categories.models import PartCategory
from app.domain.categories.tree import ParentMap, ancestor_ids
from app.domain.eda.kicad_specs import (
    custom_fields_by_part,
    rules_by_category,
    wanted_custom_field_keys,
)
from app.domain.eda.value_template import (
    MPN_PLACEHOLDER,
    PLACEHOLDER_PATTERN,
    render_value,
)
from app.domain.parts.models import Part

__all__ = [
    "ALIAS_CUSTOM_FIELD_KEY",
    "CanonicalResult",
    "NAME_MAX_LENGTH",
    "NameClass",
    "PreservedIn",
    "RenameProposal",
    "ROLE_SEPARATOR",
    "canonical_name",
    "canonical_names",
    "classify_name",
    "propose_rename",
]

# How a part's five possible names classify. The vocabulary the
# `part-rename` job reports against:
#
# canonical    — already the name the convention wants.
# mpn          — the part number, which is the convention for everything
#                without a category template, and the fallback for
#                everything whose template cannot render yet.
# description  — the provider's marketing copy, copied into the name by
#                imports predating this convention. 127 prod rows.
# role_suffix  — `<canonical or mpn> - <role>`: the right identity with a
#                project's job for the part appended. The role is what
#                the rename has to preserve.
# free         — anything else. Hand-typed, and the only place the part's
#                name is the sole copy of what somebody wrote.
NameClass = Literal["canonical", "mpn", "description", "role_suffix", "free"]

# What separates a canonical head from a project role in a legacy name.
# Spaced on both sides on purpose: `RC0603FR-0710KL` is full of hyphens
# and none of them start a role.
ROLE_SEPARATOR = " - "

# Width of `parts.name` (`domain/parts/models.py`).
NAME_MAX_LENGTH = 300

# Where a rename parks the text it is about to overwrite. A `manual`
# custom field rather than a column: it is user prose, it is not part of
# the part's identity, and no provider refresh may ever touch it
# (`provider_fields.py::provider_owns_custom_field_key` leaves `manual`
# rows alone).
ALIAS_CUSTOM_FIELD_KEY = "alias"


@dataclass(frozen=True)
class CanonicalResult:
    """The canonical name for one part, and whether a rule even applied.

    The two are separate questions and the rename job reports them
    separately: `name is None` with `has_template` false means "this
    part's category says nothing about naming", which is fine and
    permanent; `name is None` with `has_template` true means "there is a
    template and this part cannot satisfy it", which is the count that
    says how much the spec backfill still owes.
    """

    name: str | None = None
    has_template: bool = False

    @property
    def unrenderable(self) -> bool:
        """A template applied and produced nothing."""
        return self.has_template and self.name is None


# Where the old name survives once the rename has run. The rename job
# reports it per part, because "nothing was lost" has to be checkable
# afterwards rather than argued about beforehand.
#
# alias       — parked in the part's `alias` custom field by this job.
# mpn         — the old name WAS the MPN, which has its own column.
# description — the old name is still the part's `description`. Weaker
#               than the other two: `description` is provider-owned on a
#               linked part, so a later refresh can rewrite it. Only used
#               when the `alias` slot was already taken.
# none        — nothing to preserve, because nothing is being renamed.
PreservedIn = Literal["alias", "mpn", "description", "none"]


@dataclass(frozen=True)
class RenameProposal:
    """What the rename job would do to one part."""

    classification: NameClass
    # None when there is nothing to rename to (no template, no MPN) or
    # the part already holds the right name. Never the empty string.
    new_name: str | None = None
    # Text the rename would otherwise destroy, to be parked in the
    # `alias` custom field. None when the old name is recoverable from
    # another column.
    alias: str | None = None
    preserved_in: PreservedIn = "none"
    # Why `new_name` is None despite there being a name to move away
    # from. Empty when the part simply needs no rename. The job adds its
    # own reasons on top of this one.
    skip_reason: str = ""


def canonical_name(
    db: Session,
    *,
    workspace_id: UUID,
    part: Part,
    workspace_categories: Sequence[PartCategory] | None = None,
) -> str | None:
    """The name the convention wants for `part`, or None.

    None whenever the specs are insufficient to render the category's
    template, or the category has none — the caller falls back to the
    MPN. Batch callers want `canonical_names`; this is the one-part
    convenience the create paths use.
    """
    # `.get`, because a part outside `workspace_id` is dropped rather
    # than answered — see `canonical_names`.
    results = canonical_names(
        db,
        workspace_id=workspace_id,
        parts=[part],
        workspace_categories=workspace_categories,
    )
    return results.get(part.id, CanonicalResult()).name


def canonical_names(
    db: Session,
    *,
    workspace_id: UUID,
    parts: Iterable[Part],
    workspace_categories: Sequence[PartCategory] | None = None,
) -> dict[UUID, CanonicalResult]:
    """`{part_id: CanonicalResult}` for every part given.

    Three queries at most for any number of parts — the categories on the
    page, their inherited rules, and one `custom_fields` read for the
    handful of canonical keys those rules name. Parts belonging to
    another workspace are answered with an empty result rather than
    looked up: `category_id` is an opaque UUID and this is the
    workspace-equality check that stops one from naming a part across the
    tenant boundary.

    `workspace_categories` is that workspace's category rows already in
    hand. It takes the count to one query — the `custom_fields` read —
    which is what a caller creating parts in a loop needs: provider
    import runs inside bulk-import-from-scan, where a per-part category
    read would be an N+1 the batch's own snapshot already paid for.
    Archived rows may be included; they are filtered here.
    """
    parts = [part for part in parts if part.workspace_id == workspace_id]
    results: dict[UUID, CanonicalResult] = {part.id: CanonicalResult() for part in parts}
    category_ids = {part.category_id for part in parts if part.category_id is not None}
    if not category_ids:
        return results

    if workspace_categories is None:
        categories = list(
            db.execute(
                select(PartCategory)
                .where(PartCategory.workspace_id == workspace_id)
                .where(PartCategory.id.in_(category_ids))
                # An archived category is no category at all here — the
                # same rule `kicad_specs.py` applies walking ancestors.
                .where(PartCategory.archived_at.is_(None))
            )
            .scalars()
            .all()
        )
    else:
        categories = [
            category
            for category in workspace_categories
            if category.id in category_ids
            and category.workspace_id == workspace_id
            and category.archived_at is None
        ]
    if not categories:
        return results

    rules = rules_by_category(
        db,
        workspace_id=workspace_id,
        categories=categories,
        workspace_rows=workspace_categories,
    )
    prefixes = _refdes_prefixes(
        db,
        workspace_id=workspace_id,
        categories=categories,
        workspace_rows=workspace_categories,
    )
    specs = custom_fields_by_part(
        db,
        workspace_id=workspace_id,
        part_ids=[part.id for part in parts],
        keys=wanted_custom_field_keys(rules.values()),
    )

    for part in parts:
        rule = rules.get(part.category_id) if part.category_id is not None else None
        if rule is None or rule.value_template is None:
            continue
        results[part.id] = CanonicalResult(
            name=_compose(
                template=rule.value_template,
                prefix=prefixes.get(part.category_id),
                specs=specs.get(part.id, {}),
                mpn=part.mpn,
            ),
            has_template=True,
        )
    return results


def classify_name(part: Part, canonical: str | None) -> NameClass:
    """Which of the five shapes `part.name` currently has.

    `canonical` is what `canonical_name` returned for this part, which is
    None for most of the catalogue — the classes below degrade to reading
    the MPN as the head, which is what the convention names those parts
    anyway.
    """
    name = _clean(part.name)
    if canonical and name == canonical:
        return "canonical"
    mpn = _clean(part.mpn)
    if mpn and name == mpn:
        return "mpn"
    description = _clean(part.description)
    # The 300-char cut as well as the whole string: the import this
    # convention replaces truncated the description into `name`, so a
    # long provider description left a name that matches only its head.
    if name and name in (description, description[:NAME_MAX_LENGTH]):
        return "description"
    for head in (canonical, mpn):
        if head and _role_after(name, head):
            return "role_suffix"
    return "free"


def propose_rename(part: Part, canonical: str | None) -> RenameProposal:
    """What the rename job would do to `part`, decided but not applied.

    Pure: it does not know whether the `alias` slot is free, so it says
    what it would park there and the job downgrades the proposal when
    the slot is taken.
    """
    classification = classify_name(part, canonical)
    name = _clean(part.name)
    mpn = _clean(part.mpn)
    target = canonical or mpn or None
    if target is None or name == target:
        # Nothing to rename to (a local part with a hand-typed name and
        # no MPN is already the only identity it has), or already right.
        return RenameProposal(classification=classification)
    if len(target) > NAME_MAX_LENGTH:
        # Unreachable at today's widths — `parts.mpn` is String(200) and
        # `_compose` caps a rendered name — and here so that widening
        # either one turns into a skipped part rather than a `DataError`
        # 500 at flush time.
        return RenameProposal(classification=classification, skip_reason="name_too_long")

    if name == mpn:
        # The old name IS the part number, which keeps its own column.
        return RenameProposal(
            classification=classification, new_name=target, preserved_in="mpn"
        )

    # Everything else parks the old text. `description` used to be
    # treated as self-preserving, but it is provider-owned on a linked
    # part and a refresh rewrites it, after which the old name would
    # exist nowhere. So: an alias whenever the old name is neither the
    # new name nor the MPN.
    if classification == "role_suffix":
        # Only the role — the head is the new name, so storing it twice
        # would make `alias` read as a second full name.
        alias = _role_after(name, canonical or "") or _role_after(name, mpn)
    else:
        alias = name or None
    return RenameProposal(
        classification=classification,
        new_name=target,
        alias=alias,
        preserved_in="alias" if alias else "none",
    )


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _role_after(name: str, head: str) -> str | None:
    """The role in `<head> - <role>`, or None when that is not the shape."""
    if not head or not name.startswith(head + ROLE_SEPARATOR):
        return None
    return name[len(head) + len(ROLE_SEPARATOR) :].strip() or None


def _compose(
    *,
    template: str,
    prefix: str | None,
    specs: dict[str, str],
    mpn: str | None,
) -> str | None:
    """`R` + `10 kΩ 1% 0603`, or None unless the template renders whole.

    **Every placeholder must resolve.** `render_value` is deliberately
    forgiving — it drops a missing spec and collapses the whitespace, so
    a KiCad `Value` still reads well on a half-specified part. A *name*
    cannot do that. `{resistance} {tolerance} {package}` on a part
    carrying only `package` renders `0603`, and this function would
    hand back `R 0603` as the canonical identity of that part; run the
    rename over a workspace of half-specified passives and every 0603
    resistor ends up named `R 0603`. A partial render is therefore no
    name at all, and the caller falls back to the MPN, which at least
    identifies the component.
    """
    if not _renders_whole(template, specs, mpn):
        return None
    rendered = render_value(template, specs, mpn)
    if rendered is None:
        return None
    if rendered == _clean(mpn):
        # `{mpn}` is the sensible default template for everything that is
        # not a passive, and a class letter in front of a manufacturer
        # part number is noise, not identity.
        return rendered
    prefix = _clean(prefix)
    name = f"{prefix} {rendered}" if prefix else rendered
    if len(name) > NAME_MAX_LENGTH:
        # Unreachable at today's widths (`MAX_VALUE_LENGTH` 200 + a
        # `String(10)` prefix + a space), and here so that widening
        # either one cannot turn a rendered name into a `DataError` 500.
        # Refused rather than truncated, for the same reason
        # `render_value` refuses: half a unit is worse than no name.
        return None
    return name


def _renders_whole(template: str, specs: Mapping[str, str], mpn: str | None) -> bool:
    """Whether every placeholder in `template` has something to show.

    Reads the pattern directly rather than `placeholder_keys`, which
    drops `{mpn}` — a template that names the MPN on a part without one
    is exactly as unrenderable as one naming a spec the part lacks.
    """
    for key in PLACEHOLDER_PATTERN.findall(template):
        raw = mpn if key == MPN_PLACEHOLDER else specs.get(key)
        if not (raw or "").strip():
            return False
    return True


def _refdes_prefixes(
    db: Session,
    *,
    workspace_id: UUID,
    categories: Iterable[PartCategory],
    workspace_rows: Sequence[PartCategory] | None = None,
) -> dict[UUID, str | None]:
    """Each category's class letter, inherited from the nearest ancestor.

    Mirrors how `kicad_specs.py` resolves `value_template`, including
    both escape hatches: no query at all unless some category on the page
    has no prefix of its own AND a parent to inherit one from, and none
    either when the caller already holds the workspace's rows.
    """
    categories = list(categories)
    own = {category.id: (category.refdes_prefix or None) for category in categories}
    if not any(
        own[category.id] is None and category.parent_id is not None
        for category in categories
    ):
        return own

    if workspace_rows is not None:
        rows = [
            row
            for row in workspace_rows
            if row.workspace_id == workspace_id and row.archived_at is None
        ]
    else:
        rows = db.execute(
            select(
                PartCategory.id,
                PartCategory.parent_id,
                PartCategory.refdes_prefix,
            )
            .where(PartCategory.workspace_id == workspace_id)
            .where(PartCategory.archived_at.is_(None))
        ).all()
    ancestors = {row.id: (row.refdes_prefix or None) for row in rows}
    parent_map: ParentMap = {row.id: row.parent_id for row in rows}

    return {
        category_id: prefix
        if prefix is not None
        else _inherited_prefix(category_id, ancestors, parent_map)
        for category_id, prefix in own.items()
    }


def _inherited_prefix(
    category_id: UUID,
    ancestors: dict[UUID, str | None],
    parent_map: ParentMap,
) -> str | None:
    for ancestor_id in ancestor_ids(parent_map, category_id):
        if ancestor_id not in ancestors:
            # Archived, or gone. It is not part of the tree here, so
            # nothing above it is either.
            return None
        inherited = ancestors[ancestor_id]
        if inherited:
            return inherited
    return None
