"""Category-hierarchy walks — cycle guard, depth cap, descendant expansion.

**Everything here happens in Python, on purpose.** `part_categories` is an
adjacency list (`parent_id`, alembic 0078), and the obvious way to ask
"what are this node's ancestors / descendants" of an adjacency list is a
recursive CTE. This repo has never had one, and a category tree is not the
place to introduce the first: every question this module answers falls out
of a single `dict[id, parent_id]` map, and a dict lookup per level beats a
query plan nobody in this codebase can review.

The map is loaded once per request that needs it and reused for every walk
in that request — `validate_parent` does three walks off one load.

**On its size.** `load_parent_map` is deliberately *un*capped, unlike every
other read in this domain (`list_categories` caps at 200, its route at
1000): a truncated map would silently drop descendants from a filter and
hide cycles from the guard, which is worse than a large one. It is bounded
only by how many categories a workspace has created — a hand-curated
library, in practice a few hundred — and the projection is two UUIDs per
row, so even an implausible 100k rows is a few MB. If a workspace ever
makes that assumption false, the answer is a real cap on category creation
(and then a recursive CTE), not a silent `LIMIT` here.

Three rules the write paths enforce, in the order they're cheapest to
check:

  1. **No self-parent.** `x.parent_id = x` is its own degenerate cycle and
     `ancestor_ids` would never terminate on it without the visited-set.
  2. **No cycles.** Reparenting `x` under one of `x`'s own descendants
     would detach that whole component from every root — the rows would
     still exist, still be workspace-scoped, and be unreachable from any
     tree render. Checked by asking whether the *proposed parent* has `x`
     among its ancestors.
  3. **Depth cap.** A tree deeper than `MAX_DEPTH` is a UI problem (the
     rail runs out of horizontal room) long before it is a data problem.
     Reparenting checks `depth(new parent) + 1 + height(moved subtree)`,
     not just the moved node — dragging a 3-deep branch under a 5-deep
     parent has to fail even though the moved node itself would only land
     at depth 6.

Every walk carries a visited-set. Nothing should be able to write a cycle
(that is rule 2's whole job), but a walk that can hang the request thread
on malformed data is not worth the two lines it saves — and `parent_id`
survives raw SQL and restored backups that this module never saw.

**Concurrency.** Reads run at READ COMMITTED (`infra/db.py` sets no
isolation level), so "validate against a snapshot, then write" is a TOCTOU:
two simultaneous requests moving A under B and B under A each validate
against a cycle-free map and both commit, leaving a component detached from
every root. Same shape as the ledger race in `domain/stock/service.py`, and
the same fix — `pg_advisory_xact_lock`, here keyed on the workspace alone,
taken before the map is read and released at COMMIT. Category writes are
rare and never hold another advisory lock, so a single workspace-wide lock
is both cheap and deadlock-free (one lock, always acquired first).
"""
from __future__ import annotations

from collections import deque
from typing import Any
from uuid import UUID

from fastapi import status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.errors import ErrorCodes, raise_http
from app.domain.categories.models import PartCategory

__all__ = [
    "MAX_DEPTH",
    "ParentMap",
    "lock_workspace_tree",
    "load_parent_map",
    "ancestor_ids",
    "depth_of",
    "subtree_height",
    "descendant_ids",
    "validate_parent",
]

# Root categories sit at depth 1, so this allows "Passives / Resistors /
# Thin film / 0402 / 1% / 10k" and refuses a seventh level.
MAX_DEPTH = 6

# `id -> parent_id or None`, for every category in one workspace.
ParentMap = dict[UUID, UUID | None]


def lock_workspace_tree(db: Session, *, workspace_id: UUID) -> None:
    """Serialise concurrent shape changes to one workspace's category tree.

    Must be taken **before** any read the write then depends on — the
    parent map, and the parent row's own `archived_at` — or the state it
    protects is already stale. Released automatically at COMMIT / ROLLBACK.
    Keyed the same way `domain/stock/service.py` keys its per-part lock —
    UUID's `__str__` is stable canonical form, so a workspace always hashes
    to the same int8 lock id.
    """
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"category-tree:{workspace_id}"},
    )


def load_parent_map(db: Session, *, workspace_id: UUID) -> ParentMap:
    """Load `(id, parent_id)` for one workspace's categories.

    Workspace-scoped like every other read in this domain — the map is the
    only thing the walks below can see, so a row from another workspace
    could never be reached even if a `parent_id` somehow pointed at one
    (the 0078 trigger makes that impossible anyway).

    Archived categories are included deliberately. An archived row is still
    a real row with a real `parent_id`, so leaving it out would make a
    cycle through an archived node invisible to `validate_parent` and let a
    restore resurrect an unreachable component.
    """
    rows = db.execute(
        select(PartCategory.id, PartCategory.parent_id).where(
            PartCategory.workspace_id == workspace_id
        )
    ).all()
    return {row[0]: row[1] for row in rows}


def ancestor_ids(parent_map: ParentMap, category_id: UUID) -> list[UUID]:
    """Ancestors of `category_id`, nearest parent first. Excludes itself."""
    out: list[UUID] = []
    seen: set[UUID] = {category_id}
    current = parent_map.get(category_id)
    while current is not None and current not in seen:
        seen.add(current)
        out.append(current)
        current = parent_map.get(current)
    return out


def depth_of(parent_map: ParentMap, category_id: UUID) -> int:
    """1 for a root, 2 for its children, …"""
    return 1 + len(ancestor_ids(parent_map, category_id))


def _children_map(parent_map: ParentMap) -> dict[UUID, list[UUID]]:
    children: dict[UUID, list[UUID]] = {}
    for node, parent in parent_map.items():
        if parent is not None:
            children.setdefault(parent, []).append(node)
    return children


def descendant_ids(parent_map: ParentMap, category_id: UUID) -> set[UUID]:
    """`category_id` plus everything beneath it.

    Includes the root itself so callers can use the result directly as an
    `IN (…)` set — "parts in Passives" means parts filed under Passives as
    well as under its subcategories.
    """
    children = _children_map(parent_map)
    out: set[UUID] = {category_id}
    queue = deque([category_id])
    while queue:
        node = queue.popleft()
        for child in children.get(node, ()):
            if child not in out:
                out.add(child)
                queue.append(child)
    return out


def subtree_height(parent_map: ParentMap, category_id: UUID) -> int:
    """Levels *below* `category_id`. A leaf has height 0."""
    children = _children_map(parent_map)
    height = 0
    seen: set[UUID] = {category_id}
    level = [category_id]
    while level:
        next_level = [
            child
            for node in level
            for child in children.get(node, ())
            if child not in seen
        ]
        seen.update(next_level)
        if not next_level:
            break
        height += 1
        level = next_level
    return height


def validate_parent(
    db: Session,
    *,
    ws: Any,
    parent_id: UUID | None,
    category_id: UUID | None = None,
) -> None:
    """Guard a create (`category_id=None`) or a reparent.

    Raises 404 for a parent outside this workspace (never 403 — a foreign
    UUID must be indistinguishable from a missing one, ADR-0002), 409 for
    an archived parent, and 422 for self-parent / cycle / too deep.
    Returns None; the caller assigns `parent_id` itself.
    """
    if parent_id is None:
        return

    if category_id is not None and parent_id == category_id:
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=ErrorCodes.CATEGORY_PARENT_CYCLE,
            message="a category cannot be its own parent",
        )

    # Import here: `service` imports this module for `archive_category`'s
    # child promotion, so a module-level import back into `service` would
    # be circular.
    from app.domain.categories.service import get_category

    # Lock before *any* read this decision rests on, not just the map:
    # without it two concurrent reparents each validate against a
    # cycle-free snapshot and both commit one, and a concurrent archive
    # could retire the parent between the check below and the write. Held
    # to COMMIT, so the caller's subsequent write is covered too.
    lock_workspace_tree(db, workspace_id=ws.id)

    parent = get_category(db, ws=ws, category_id=parent_id)
    if parent.archived_at is not None:
        raise_http(
            status.HTTP_409_CONFLICT,
            code=ErrorCodes.CATEGORY_ARCHIVED,
            message=f'parent category "{parent.name}" is archived',
        )

    parent_map = load_parent_map(db, workspace_id=ws.id)
    # The row being moved may not be in the map yet (create), and if it is,
    # its *stored* parent is about to be replaced. Neither matters for the
    # walks below: both start from `parent_id` and only look upwards.
    if category_id is not None and category_id in ancestor_ids(parent_map, parent_id):
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=ErrorCodes.CATEGORY_PARENT_CYCLE,
            message=(
                f'"{parent.name}" is already below this category; '
                "moving it here would create a cycle"
            ),
        )

    # A create lands a leaf (height 0). A reparent carries its subtree.
    height = subtree_height(parent_map, category_id) if category_id is not None else 0
    resulting_depth = depth_of(parent_map, parent_id) + 1 + height
    if resulting_depth > MAX_DEPTH:
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=ErrorCodes.CATEGORY_TOO_DEEP,
            message=(
                f"category nesting is limited to {MAX_DEPTH} levels; "
                f"this would create {resulting_depth}"
            ),
            max_depth=MAX_DEPTH,
        )
