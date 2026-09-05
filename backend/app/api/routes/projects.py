from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import or_, select

from app.api._helpers import assert_child_in_parent, assert_in_workspace, require_resource_access
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.errors import ErrorCodes, raise_http
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import ok
from app.core.time import utcnow
from app.domain._quantity import quantity_out
from app.domain.parts.models import Part
from app.domain.projects import bom_import as bom
from app.domain.projects import bom_import_provider
from app.domain.projects.models import Project, ProjectEntry
from app.domain.projects.schemas import (
    BomEntryIn,
    BomEntryPatch,
    BomImportCommitIn,
    BomImportPreviewIn,
    BomProviderImportChoiceIn,
    BomProviderImportIn,
    MatchEntryIn,
    ProjectCreateIn,
    ProjectPatchIn,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _serialize(p: Project) -> dict:
    return {
        "id": str(p.id),
        "name": p.name,
        "description": p.description,
        "notes_markdown": p.notes_markdown,
        "associated_subassembly_part_id": str(p.associated_subassembly_part_id) if p.associated_subassembly_part_id else None,
        "archived_at": p.archived_at.isoformat() if p.archived_at else None,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


def _serialize_entry(e: ProjectEntry) -> dict:
    return {
        "id": str(e.id),
        "project_id": str(e.project_id),
        "entry_type": e.entry_type,
        "part_id": str(e.part_id) if e.part_id else None,
        "meta_part_id": str(e.meta_part_id) if e.meta_part_id else None,
        "name": e.name,
        "quantity": quantity_out(e.quantity) if e.quantity is not None else 0,
        "attrition_pct": float(e.attrition_pct) if e.attrition_pct is not None else 0,
        "comments": e.comments,
        "designators": e.designators or [],
        "cad_footprint": e.cad_footprint,
        "cad_key": e.cad_key,
        "dnp": e.dnp,
        "order_index": e.order_index,
    }


@router.get("")
def list_projects(
    db: DbSession,
    ws: CurrentWorkspace,
    archived: bool = False,
    q: str | None = None,
    limit: int = Query(default=200, le=1000),
):
    stmt = select(Project).where(Project.workspace_id == ws.id)
    stmt = stmt.where(Project.archived_at.is_(None) if not archived else Project.archived_at.is_not(None))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Project.name.ilike(like), Project.description.ilike(like)))
    stmt = stmt.order_by(Project.updated_at.desc()).limit(limit)
    return ok([_serialize(p) for p in db.execute(stmt).scalars()])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreateIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    if payload.associated_subassembly_part_id is not None:
        _assert_part_live(db, payload.associated_subassembly_part_id, ws.id)
    p = Project(
        workspace_id=ws.id,
        name=payload.name,
        description=payload.description,
        notes_markdown=payload.notes_markdown,
        associated_subassembly_part_id=payload.associated_subassembly_part_id,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(p)
    db.flush()
    return ok(_serialize(p))


def _get(db, ws_id, pid) -> Project:
    try:
        return assert_in_workspace(db, Project, pid, ws_id, label="project")
    except HTTPException:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCodes.PROJECT_NOT_FOUND,
            message="project not found",
        )


@router.get("/{project_id}")
def get_project(project_id: UUID, db: DbSession, ws: CurrentWorkspace):
    return ok(_serialize(_get(db, ws.id, project_id)))


@router.patch("/{project_id}")
def patch_project(project_id: UUID, payload: ProjectPatchIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    p = _get(db, ws.id, project_id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("associated_subassembly_part_id") is not None:
        _assert_part_live(db, data["associated_subassembly_part_id"], ws.id)
    for k, v in data.items():
        setattr(p, k, v)
    p.updated_by = user.id
    return ok(_serialize(p))


# Archive/restore — `require_resource_access` enforces resource-existence
# BEFORE the role check (BE2-009). A non-admin probing a foreign
# workspace's project_id gets 404, not 403.
@router.post("/{project_id}/archive")
def archive_project(project_id: UUID, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from app.domain.attachments.models import Attachment
    from app.domain.custom_fields.models import CustomField as CF
    from app.domain.tags.models import TagLink

    p = require_resource_access(
        db, Project, project_id, ws=ws, user=user, role="admin", label="project"
    )
    p.archived_at = utcnow()

    def _count(Model, ws_id, obj_id):
        return db.execute(
            sa_select(func.count()).select_from(Model).where(
                Model.workspace_id == ws_id,
                Model.object_id == obj_id,
            )
        ).scalar_one()

    logger.info(
        "project archived",
        extra={
            "workspace_id": str(ws.id),
            "project_id": str(p.id),
            "polymorphic_attachments": _count(Attachment, ws.id, p.id),
            "polymorphic_custom_fields": _count(CF, ws.id, p.id),
            "polymorphic_tag_links": _count(TagLink, ws.id, p.id),
        },
    )
    return ok(None, "archived")


@router.post("/{project_id}/restore")
def restore_project(project_id: UUID, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    p = require_resource_access(
        db, Project, project_id, ws=ws, user=user, role="admin", label="project"
    )
    p.archived_at = None
    return ok(None, "restored")


# --------- BOM ----------
@router.get("/{project_id}/entries")
def list_entries(project_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get(db, ws.id, project_id)
    rows = list(
        db.execute(
            select(ProjectEntry).where(ProjectEntry.workspace_id == ws.id).where(ProjectEntry.project_id == p.id).order_by(ProjectEntry.order_index)
        ).scalars()
    )
    return ok([_serialize_entry(e) for e in rows])


def _assert_part_live(db, part_id: UUID, workspace_id: UUID) -> None:
    """Assert a part exists in the workspace AND is not archived. The
    400/404 oracle distinction matches `_get_part(include_archived=False)`
    in parts.py: the archived-but-real case 404s rather than leaking
    "this id exists, just retired" (BE2-016)."""
    part = assert_in_workspace(db, Part, part_id, workspace_id, label="part")
    if part.archived_at is not None:
        raise_http(404, code=ErrorCodes.PART_NOT_FOUND, message="part not found")


@router.post("/{project_id}/entries")
def add_entry(project_id: UUID, payload: BomEntryIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    p = _get(db, ws.id, project_id)
    # Refuse archived parts on add — binding a retired part into a BOM
    # would mislead later builds (BE2-016).
    if payload.part_id is not None:
        _assert_part_live(db, payload.part_id, ws.id)
    if payload.meta_part_id is not None:
        _assert_part_live(db, payload.meta_part_id, ws.id)
    next_idx = (
        db.execute(
            select(ProjectEntry.order_index)
            .where(ProjectEntry.workspace_id == ws.id)
            .where(ProjectEntry.project_id == p.id)
            .order_by(ProjectEntry.order_index.desc())
            .limit(1)
        ).scalar() or 0
    ) + 1
    e = ProjectEntry(
        workspace_id=ws.id,
        project_id=p.id,
        entry_type=payload.entry_type,
        part_id=payload.part_id,
        meta_part_id=payload.meta_part_id,
        name=payload.name or "",
        quantity=payload.quantity,
        attrition_pct=payload.attrition_pct,
        comments=payload.comments,
        designators=payload.designators or [],
        cad_footprint=payload.cad_footprint,
        cad_key=payload.cad_key,
        dnp=payload.dnp,
        order_index=next_idx,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(e)
    db.flush()
    return ok(_serialize_entry(e))


@router.patch("/{project_id}/entries/{entry_id}")
def patch_entry(project_id: UUID, entry_id: UUID, payload: BomEntryPatch, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    p = _get(db, ws.id, project_id)
    e = assert_child_in_parent(db, ProjectEntry, entry_id, p, parent_fk="project_id", label="entry")
    data = payload.model_dump(exclude_unset=True)
    # Same archived-part guard as add_entry — patch-binds an archived
    # part into the BOM would be the BE2-016 vector via PATCH.
    if data.get("part_id") is not None:
        _assert_part_live(db, data["part_id"], ws.id)
    if data.get("meta_part_id") is not None:
        _assert_part_live(db, data["meta_part_id"], ws.id)
    for k, v in data.items():
        setattr(e, k, v)
    e.updated_by = user.id
    return ok(_serialize_entry(e))


@router.delete("/{project_id}/entries/{entry_id}")
def del_entry(project_id: UUID, entry_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get(db, ws.id, project_id)
    e = assert_child_in_parent(db, ProjectEntry, entry_id, p, parent_fk="project_id", label="entry")
    db.delete(e)
    return ok(None)


# --------- BOM import ----------
@router.post("/{project_id}/bom/preview")
def preview_bom(project_id: UUID, payload: BomImportPreviewIn, db: DbSession, ws: CurrentWorkspace):
    _get(db, ws.id, project_id)
    return ok(bom.preview(payload, db=db, workspace_id=ws.id).model_dump())


@router.post("/{project_id}/bom/import")
def commit_bom(project_id: UUID, payload: BomImportCommitIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    project = _get(db, ws.id, project_id)
    # `get_db` rolls back on any raised exception, so the explicit
    # try/except db.rollback() shape becomes redundant — we just let
    # the dep handle it.
    result = bom.commit(db, workspace_id=ws.id, user_id=user.id, project=project, payload=payload)
    return ok(result.model_dump())


@router.post("/{project_id}/bom/import-from-provider")
@limiter.limit("30/minute", key_func=workspace_key)
def import_bom_from_provider(
    request: Request,
    project_id: UUID,
    payload: BomProviderImportIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    project = assert_in_workspace(db, Project, project_id, ws.id, label="project")
    result = bom_import_provider.import_unmatched_from_provider(
        db,
        workspace=ws,
        user_id=user.id,
        project=project,
        entry_ids=payload.entry_ids,
    )
    return ok(result.model_dump(mode="json"))


@router.post("/{project_id}/bom/import-from-provider/commit-choices")
@limiter.limit("30/minute", key_func=workspace_key)
def commit_bom_provider_choices(
    request: Request,
    project_id: UUID,
    payload: BomProviderImportChoiceIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    project = assert_in_workspace(db, Project, project_id, ws.id, label="project")
    result = bom_import_provider.commit_provider_import_choices(
        db,
        workspace=ws,
        user_id=user.id,
        project=project,
        choices=payload.choices,
    )
    return ok(result.model_dump(mode="json"))


@router.post("/{project_id}/entries/{entry_id}/match")
def match_entry(project_id: UUID, entry_id: UUID, payload: MatchEntryIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    p = _get(db, ws.id, project_id)
    e = assert_child_in_parent(db, ProjectEntry, entry_id, p, parent_fk="project_id", label="entry")
    part = assert_in_workspace(db, Part, payload.part_id, ws.id, label="part")
    if part.archived_at is not None:
        # Match the new add/patch_entry guard — match-bind an archived
        # part is the same BE2-016 vector with a different verb.
        raise_http(404, code=ErrorCodes.PART_NOT_FOUND, message="part not found")
    e.part_id = part.id
    e.entry_type = "part"
    e.updated_by = user.id
    return ok(_serialize_entry(e))
