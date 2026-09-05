"""Replace a part across projects (mirrors PartsBox's early-2026 feature).

From a source part, repoint every matching BOM line to a replacement part
across some or all of the workspace's projects, in one transaction.

Lives in the projects domain because the mutation is entirely against
`project_entries` rows — the part router simply exposes it as a per-part
action (`POST /api/parts/{part_id}/replace-in-projects`).

Workspace isolation is enforced here in code (CLAUDE.md invariant): the
source/target parts are resolved by the caller through the workspace-scoped
`get_part` helper, and every project id — whether explicitly supplied or
discovered as "all projects" — is filtered by `workspace_id == ws.id`. No
row belonging to another workspace is ever read or written.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api._helpers import assert_in_workspace
from app.core.time import utcnow
from app.domain.audit.service import log as _audit_log
from app.domain.parts.models import Part
from app.domain.projects.models import Project, ProjectEntry
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace


@dataclass(frozen=True)
class ReplaceResult:
    """Outcome of a replace-in-projects run.

    `updated_entries` is the total number of BOM lines repointed;
    `affected_projects` counts the distinct projects that had at least one
    line changed (projects in scope with zero matching lines are neither
    counted nor audited).
    """

    updated_entries: int
    affected_projects: int

    def as_dict(self) -> dict[str, int]:
        return {
            "updated_entries": self.updated_entries,
            "affected_projects": self.affected_projects,
        }


def _resolve_target_projects(
    db: Session,
    *,
    workspace_id: UUID,
    project_ids: list[UUID] | None,
) -> list[Project]:
    """Projects the replacement should touch, all scoped to the workspace.

    Omitted / empty `project_ids` means "every active project in the
    workspace" — archived projects are deliberately excluded from the
    implicit all-projects sweep so a bulk replace never resurrects a
    reference inside a retired BOM. An explicit id list is honoured
    verbatim (archived or not), because naming a project is an intentional
    choice; each named id is validated against the workspace and 404s if it
    belongs to another tenant.
    """
    if project_ids:
        # De-duplicate while preserving order so a repeated id doesn't
        # double-audit the same project.
        seen: set[UUID] = set()
        projects: list[Project] = []
        for pid in project_ids:
            if pid in seen:
                continue
            seen.add(pid)
            projects.append(
                assert_in_workspace(db, Project, pid, workspace_id, label="project")
            )
        return projects

    return list(
        db.execute(
            select(Project)
            .where(Project.workspace_id == workspace_id)
            .where(Project.archived_at.is_(None))
        ).scalars()
    )


def replace_part_in_projects(
    db: Session,
    *,
    workspace: Workspace,
    user: User,
    source_part: Part,
    target_part: Part,
    project_ids: list[UUID] | None,
    request_id: str | None = None,
) -> ReplaceResult:
    """Repoint every `project_entries.part_id == source` to `target`.

    Callers MUST resolve `source_part` and `target_part` through the
    workspace-scoped part helper first, and MUST have already rejected the
    source == target case — this function assumes both parts are live in
    `workspace` and distinct.

    Writes one audit row per affected project (matching the per-object
    granularity used elsewhere in the parts router) plus one summary row on
    the source part, all inside the caller's transaction.
    """
    projects = _resolve_target_projects(
        db, workspace_id=workspace.id, project_ids=project_ids
    )

    now = utcnow()
    total_updated = 0
    affected_project_ids: list[UUID] = []

    for project in projects:
        # Scope the UPDATE by workspace_id AND project_id AND part_id so it
        # can never reach across tenants even if a Project row were somehow
        # mis-scoped. `synchronize_session=False` is safe: nothing in this
        # request re-reads the mutated entries from the identity map.
        result = db.execute(
            update(ProjectEntry)
            .where(ProjectEntry.workspace_id == workspace.id)
            .where(ProjectEntry.project_id == project.id)
            .where(ProjectEntry.part_id == source_part.id)
            .values(part_id=target_part.id, updated_by=user.id, updated_at=now)
        )
        count = result.rowcount or 0
        if count == 0:
            continue
        total_updated += count
        affected_project_ids.append(project.id)
        _audit_log(
            db,
            ws=workspace,
            user=user,
            action="project.part_replaced",
            target_type="project",
            # Stable ids only; part UUIDs are not sensitive. The comment
            # keeps to a low-sensitivity count summary per the audit invariant.
            target_ids=[project.id, source_part.id, target_part.id],
            comment=f"entries={count}",
            request_id=request_id,
        )

    # One summary row on the source part so the part's own activity timeline
    # records the bulk operation even when it spanned many projects.
    _audit_log(
        db,
        ws=workspace,
        user=user,
        action="part.replaced_in_projects",
        target_type="part",
        target_ids=[source_part.id, target_part.id],
        comment=f"projects={len(affected_project_ids)} entries={total_updated}",
        request_id=request_id,
    )

    return ReplaceResult(
        updated_entries=total_updated,
        affected_projects=len(affected_project_ids),
    )
