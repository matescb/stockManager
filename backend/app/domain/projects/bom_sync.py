"""Project BOM synchronisation in place (SM-011).

`POST /api/projects/{id}/bom/sync` and the MCP tool `sync_project_bom`
both land here. The CSV importer (`bom_import.commit`) only ever APPENDS
lines, so the one push path that existed deleted every entry and
re-imported — which cascades through `build_stage_lines`
(`ON DELETE CASCADE`) and the purchase-plan lines, and throws away every
per-line decision (attrition, a manual part match, comments). This
module diffs the incoming rows against the project's lines and touches
only what changed.

Line identity
-------------
Each incoming row and each existing line gets one key:

1. **Designators** — when the row has any: the set of references,
   stripped, upper-cased, de-duplicated and sorted (`R1, r2` and `R2 R1`
   are the same line). Splitting or merging a group (`R1,R2` becomes
   `R1` + `R2`) is therefore a removal plus additions, not an update.
2. otherwise the **matched part id** (a line with no references, e.g. a
   "glue" or "label" line),
3. otherwise the row's **CAD key**,
4. otherwise its **label** — `name`, else `value`, else `mpn`,
   case-folded.

A row with none of these is refused (`bom.sync_unkeyed_row`), and two
rows with the same key are refused (`bom.sync_duplicate_line`): either
would make the diff depend on row order.

The diff
--------
* **unchanged** — an active line with the row's key whose fields already
  equal the row's. No write, no audit row.
* **updated** — an active line with the row's key and at least one
  differing field; updated in place (same id, so builds, stage lines and
  purchase plans keep pointing at it). One audit row naming the changed
  fields — never their values (SM-013 adds before/after).
* **added** — no active line with the key; a new line is inserted. If an
  ARCHIVED line of this project has the key, it is un-archived instead
  (`archived_at = NULL`, fields updated) and reported as added with
  `restored: true`, so a designator that leaves the schematic and comes
  back keeps its id and history.
* **removed** — an active line whose key no row has; `archived_at` is
  set, never deleted. Every reader of a project's BOM filters
  `archived_at IS NULL` (`builds/service.py`, `get_project_bom`, the
  entries route), while a build stage that already references the line
  still resolves it.
* **unmatched** — reported alongside the other four: the rows whose
  resulting line has no part link. Not exclusive — an unmatched line is
  also added, updated or unchanged.

Only fields the row carries are compared. A null `name`, `footprint`,
`cad_key`, `comments` or `dnp` leaves the existing value alone, and a
row the resolver cannot match never clears a part link someone set by
hand (`POST .../entries/{id}/match`). `attrition_pct`, `meta_part_id`
and `order_index` are never touched by a sync. `quantity` is always
compared; a row without one counts its designators (1 if none).

`dry_run=True` computes the same diff and writes nothing: the plan is
built from reads only, and the apply step is skipped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ErrorCodes, raise_http
from app.core.time import utcnow
from app.domain.audit.service import log as audit_log
from app.domain.parts.models import Part
from app.domain.projects.bom_import import ParsedRow, _match_part
from app.domain.projects.models import Project, ProjectEntry
from app.domain.projects.schemas import BomSyncRowIn
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace

AUDIT_ADDED = "project_entry.sync_added"
AUDIT_RESTORED = "project_entry.sync_restored"
AUDIT_UPDATED = "project_entry.sync_updated"
AUDIT_REMOVED = "project_entry.sync_removed"

# Optional row fields → the entry column they set when present.
_OPTIONAL_COLUMNS = (
    ("cad_footprint", "footprint"),
    ("cad_key", "cad_key"),
    ("comments", "comments"),
    ("dnp", "dnp"),
)

Key = tuple[str, str]


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _norm_designators(designators: list[str] | None) -> tuple[str, ...]:
    return tuple(sorted({d.strip().upper() for d in designators or [] if d.strip()}))


def _key(
    designators: tuple[str, ...],
    part_id: UUID | None,
    cad_key: str | None,
    label: str | None,
) -> Key | None:
    if designators:
        return ("designators", ",".join(designators))
    if part_id is not None:
        return ("part", str(part_id))
    if cad_key:
        return ("cad_key", cad_key)
    if label:
        return ("label", label.casefold())
    return None


def _entry_key(entry: ProjectEntry) -> Key | None:
    return _key(
        _norm_designators(entry.designators),
        entry.part_id,
        _clean(entry.cad_key),
        _clean(entry.name),
    )


@dataclass
class _Desired:
    """What one incoming row says its line should be."""

    key: Key
    designators: list[str]
    part: Part | None
    quantity: int
    name: str | None
    label: str | None
    optional: dict[str, Any]


@dataclass
class _Plan:
    added: list[tuple[_Desired, ProjectEntry | None, list[str]]] = field(default_factory=list)
    updated: list[tuple[_Desired, ProjectEntry, list[str]]] = field(default_factory=list)
    unchanged: list[tuple[_Desired, ProjectEntry]] = field(default_factory=list)
    removed: list[ProjectEntry] = field(default_factory=list)


def _desired(db: Session, *, workspace_id: UUID, row: BomSyncRowIn) -> _Desired | None:
    designators = [d.strip() for d in row.designators if d.strip()]
    name = _clean(row.name) or _clean(row.value)
    parsed = ParsedRow(
        part=_clean(row.name),
        mpn=_clean(row.mpn),
        manufacturer=_clean(row.manufacturer),
        internal_part_number=_clean(row.ipn),
        id_code=_clean(row.id_code),
        cad_key=_clean(row.cad_key),
    )
    part = _match_part(db, workspace_id=workspace_id, row=parsed)
    label = name or parsed.mpn
    key = _key(
        _norm_designators(designators),
        part.id if part else None,
        parsed.cad_key,
        label,
    )
    if key is None:
        return None
    optional: dict[str, Any] = {}
    for column, attr in _OPTIONAL_COLUMNS:
        value = getattr(row, attr)
        if isinstance(value, str):
            value = _clean(value) if column != "comments" else value
        if value is not None:
            optional[column] = value
    quantity = row.quantity if row.quantity is not None else (len(designators) or 1)
    return _Desired(
        key=key,
        designators=designators,
        part=part,
        quantity=quantity,
        name=name,
        label=label,
        optional=optional,
    )


def _changes(want: _Desired, entry: ProjectEntry) -> dict[str, Any]:
    """Column → new value for every field the row sets that differs."""
    out: dict[str, Any] = {}
    if want.part is not None:
        if entry.part_id != want.part.id:
            out["part_id"] = want.part.id
        relinked = "part_id" in out and entry.entry_type != "part"
        if entry.entry_type == "unmatched" or relinked:
            out["entry_type"] = "part"
    if want.name is not None and entry.name != want.name:
        out["name"] = want.name
    if Decimal(entry.quantity) != Decimal(want.quantity):
        out["quantity"] = want.quantity
    for column, value in want.optional.items():
        if getattr(entry, column) != value:
            out[column] = value
    return out


def _parse_rows(db: Session, *, workspace_id: UUID, rows: list[BomSyncRowIn]) -> list[_Desired]:
    wanted: list[_Desired] = []
    unkeyed: list[int] = []
    seen: dict[Key, int] = {}
    duplicates: list[int] = []
    for index, row in enumerate(rows):
        desired = _desired(db, workspace_id=workspace_id, row=row)
        if desired is None:
            unkeyed.append(index)
            continue
        if desired.key in seen:
            duplicates.append(index)
            continue
        seen[desired.key] = index
        wanted.append(desired)
    if unkeyed:
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            ErrorCodes.BOM_SYNC_UNKEYED_ROW,
            "BOM row(s) have no designators, no resolvable part, no cad_key "
            "and no name/value/mpn to identify the line by",
            rows=unkeyed[:50],
        )
    if duplicates:
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            ErrorCodes.BOM_SYNC_DUPLICATE_LINE,
            "BOM row(s) repeat a line already given (same designators, or "
            "same part/cad_key/name for a line without designators)",
            rows=duplicates[:50],
        )
    return wanted


def _project_entries(db: Session, *, workspace_id: UUID, project: Project) -> list[ProjectEntry]:
    return list(
        db.execute(
            select(ProjectEntry)
            .where(ProjectEntry.workspace_id == workspace_id)
            .where(ProjectEntry.project_id == project.id)
            .order_by(ProjectEntry.order_index, ProjectEntry.created_at, ProjectEntry.id)
        ).scalars()
    )


def _plan(wanted: list[_Desired], entries: list[ProjectEntry]) -> _Plan:
    active: dict[Key, ProjectEntry] = {}
    archived: dict[Key, ProjectEntry] = {}
    plan = _Plan()
    for entry in entries:
        key = _entry_key(entry)
        if entry.archived_at is None:
            # A second active line with a key already taken is a leftover
            # of an earlier append-only import; the sync retires it.
            if key is None or key in active:
                plan.removed.append(entry)
            else:
                active[key] = entry
        elif key is not None:
            previous = archived.get(key)
            if previous is None or entry.archived_at > previous.archived_at:
                archived[key] = entry

    for want in wanted:
        entry = active.pop(want.key, None)
        if entry is not None:
            changes = _changes(want, entry)
            if changes:
                plan.updated.append((want, entry, sorted(changes)))
            else:
                plan.unchanged.append((want, entry))
            continue
        restored = archived.get(want.key)
        changed = sorted(_changes(want, restored)) if restored is not None else []
        plan.added.append((want, restored, changed))
    plan.removed.extend(active.values())
    plan.removed.sort(key=lambda e: (e.order_index, str(e.id)))
    return plan


@dataclass
class _Actor:
    ws: Workspace
    user: User | None
    request_id: str | None


def _audit(db: Session, actor: _Actor, action: str, entry: ProjectEntry, comment: str) -> None:
    audit_log(
        db,
        ws=actor.ws,
        user=actor.user,
        action=action,
        target_type="project_entry",
        target_ids=[entry.id],
        comment=comment,
        request_id=actor.request_id,
    )


def _apply_changes(entry: ProjectEntry, want: _Desired, user_id: UUID | None) -> None:
    for column, value in _changes(want, entry).items():
        setattr(entry, column, value)
    entry.updated_by = user_id


def _insert(
    db: Session, *, project: Project, want: _Desired, order_index: int, user_id: UUID | None
) -> ProjectEntry:
    part = want.part
    entry = ProjectEntry(
        workspace_id=project.workspace_id,
        project_id=project.id,
        entry_type="part" if part else "unmatched",
        part_id=part.id if part else None,
        name=want.name or (part.name if part else want.label) or "",
        quantity=want.quantity,
        comments=want.optional.get("comments"),
        designators=want.designators,
        cad_footprint=want.optional.get("cad_footprint"),
        cad_key=want.optional.get("cad_key"),
        dnp=bool(want.optional.get("dnp", False)),
        order_index=order_index,
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(entry)
    db.flush()
    return entry


def _apply(db: Session, *, project: Project, plan: _Plan, actor: _Actor) -> dict[int, ProjectEntry]:
    """Write the plan. Returns the entry each `added` item ended up as."""
    user_id = actor.user.id if actor.user else None
    tag = f"bom sync, project {project.id}"
    for want, entry, changed in plan.updated:
        _apply_changes(entry, want, user_id)
        _audit(db, actor, AUDIT_UPDATED, entry, f"{tag}; fields: {', '.join(changed)}")

    next_index = db.execute(
        select(func.coalesce(func.max(ProjectEntry.order_index), -1))
        .where(ProjectEntry.workspace_id == project.workspace_id)
        .where(ProjectEntry.project_id == project.id)
    ).scalar_one() + 1
    added: dict[int, ProjectEntry] = {}
    for index, (want, restored, changed) in enumerate(plan.added):
        if restored is not None:
            restored.archived_at = None
            _apply_changes(restored, want, user_id)
            fields = f"; fields: {', '.join(changed)}" if changed else ""
            _audit(db, actor, AUDIT_RESTORED, restored, f"{tag}{fields}")
            added[index] = restored
            continue
        entry = _insert(db, project=project, want=want, order_index=next_index, user_id=user_id)
        next_index += 1
        _audit(db, actor, AUDIT_ADDED, entry, tag)
        added[index] = entry

    now = utcnow()
    for entry in plan.removed:
        entry.archived_at = now
        entry.updated_by = user_id
        _audit(db, actor, AUDIT_REMOVED, entry, tag)
    db.flush()
    return added


def _sid(value: UUID | None) -> str | None:
    return str(value) if value is not None else None


def _removed_line(entry: ProjectEntry) -> dict[str, Any]:
    return {
        "entry_id": _sid(entry.id),
        "designators": list(entry.designators or []),
        "part_id": _sid(entry.part_id),
        "name": entry.name,
    }


def _line(want: _Desired, entry: ProjectEntry | None, **extra: Any) -> dict[str, Any]:
    part_id = want.part.id if want.part else (entry.part_id if entry else None)
    designators = want.designators or (list(entry.designators or []) if entry else [])
    name = want.name or (entry.name if entry else None) or (
        want.part.name if want.part else want.label
    )
    return {
        "entry_id": _sid(entry.id) if entry is not None else None,
        "designators": designators,
        "part_id": _sid(part_id),
        "name": name,
        **extra,
    }


def _result(
    plan: _Plan, added_entries: dict[int, ProjectEntry], *, dry_run: bool
) -> dict[str, Any]:
    added = [
        _line(want, added_entries.get(i, restored), restored=restored is not None, changed=changed)
        for i, (want, restored, changed) in enumerate(plan.added)
    ]
    updated = [_line(want, entry, changed=changed) for want, entry, changed in plan.updated]
    unchanged = [_line(want, entry) for want, entry in plan.unchanged]
    removed = [_removed_line(entry) for entry in plan.removed]
    unmatched = [line for line in (*added, *updated, *unchanged) if line["part_id"] is None]
    return {
        "dry_run": dry_run,
        "added": len(added),
        "updated": len(updated),
        "removed": len(removed),
        "unchanged": len(unchanged),
        "unmatched": len(unmatched),
        "lines": {
            "added": added,
            "updated": updated,
            "removed": removed,
            "unchanged": unchanged,
            "unmatched": unmatched,
        },
    }


def sync(
    db: Session,
    *,
    ws: Workspace,
    user: User | None,
    project: Project,
    rows: list[BomSyncRowIn],
    dry_run: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Bring `project`'s BOM in line with `rows`; see the module docstring.

    The caller has already checked that `project` belongs to `ws`. Every
    query here is filtered by `ws.id` as well, and part resolution goes
    through `bom_import._match_part`, which only returns parts of `ws`.
    """
    wanted = _parse_rows(db, workspace_id=ws.id, rows=rows)
    entries = _project_entries(db, workspace_id=ws.id, project=project)
    plan = _plan(wanted, entries)
    if dry_run:
        return _result(plan, {}, dry_run=True)
    actor = _Actor(ws=ws, user=user, request_id=request_id)
    added_entries = _apply(db, project=project, plan=plan, actor=actor)
    return _result(plan, added_entries, dry_run=False)
