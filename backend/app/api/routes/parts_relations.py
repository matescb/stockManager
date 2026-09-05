"""Part substitute and meta-member relationship routes."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request, status

from app.api.routes._parts_shared import get_part as _get_part
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.errors import ErrorCodes, raise_http
from app.core.responses import ok
from app.domain.audit.service import log as _audit_log
from app.domain.parts.models import PartMetaMember, PartSubstitute
from app.domain.parts.schemas import MetaMemberIn, ReplaceInProjectsIn, SubstituteIn
from app.domain.projects.replace_part import replace_part_in_projects

router = APIRouter()


@router.post("/{part_id}/substitutes")
def add_substitute(
    request: Request,
    part_id: UUID,
    payload: SubstituteIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    # Both sides must be live parts; archived bindings are not useful.
    p = _get_part(db, ws.id, part_id)
    sub = _get_part(db, ws.id, payload.substitute_part_id)
    row = PartSubstitute(
        workspace_id=ws.id,
        part_id=p.id,
        substitute_part_id=sub.id,
        direction=payload.direction,
    )
    db.add(row)
    db.flush()
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="part.substitute_added",
        target_type="part_substitute",
        target_ids=[p.id, sub.id],
        comment=f"direction={payload.direction}",
        request_id=getattr(request.state, "request_id", None),
    )
    return ok(None)


@router.get("/{part_id}/substitutes")
def list_substitutes(part_id: UUID, db: DbSession, ws: CurrentWorkspace):
    p = _get_part(db, ws.id, part_id, include_archived=True)
    rows = db.query(PartSubstitute).filter_by(workspace_id=ws.id, part_id=p.id).all()
    return ok(
        [{"part_id": str(r.substitute_part_id), "direction": r.direction} for r in rows]
    )


@router.delete("/{part_id}/substitutes/{substitute_id}")
def del_substitute(
    request: Request,
    part_id: UUID,
    substitute_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    # Removal is allowed even on archived rows so dead bindings can be cleaned.
    p = _get_part(db, ws.id, part_id, include_archived=True)
    deleted = db.query(PartSubstitute).filter_by(
        workspace_id=ws.id,
        part_id=p.id,
        substitute_part_id=substitute_id,
    ).delete()
    if deleted:
        _audit_log(
            db,
            ws=ws,
            user=user,
            action="part.substitute_removed",
            target_type="part_substitute",
            target_ids=[p.id, substitute_id],
            comment="relation=substitute",
            request_id=getattr(request.state, "request_id", None),
        )
    return ok(None)


@router.post("/{part_id}/replace-in-projects")
def replace_in_projects(
    request: Request,
    part_id: UUID,
    payload: ReplaceInProjectsIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    """Repoint every matching BOM line from this part to a replacement.

    The source part may be archived (you often replace *because* it was
    retired), so it's resolved with `include_archived=True`; the target
    must be live, since binding an archived part into a BOM is the BE2-016
    vector the add/patch/match entry routes already guard against.
    """
    source = _get_part(db, ws.id, part_id, include_archived=True)
    # Compare ids before resolving the target so "replace X with X" always
    # 400s — even when X is archived (where the live-only target lookup
    # would otherwise 404 and hide the real mistake).
    if payload.target_part_id == source.id:
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            code=ErrorCodes.PART_REPLACE_SAME_TARGET,
            message="replacement part must differ from the source part",
        )
    target = _get_part(db, ws.id, payload.target_part_id)
    result = replace_part_in_projects(
        db,
        workspace=ws,
        user=user,
        source_part=source,
        target_part=target,
        project_ids=payload.project_ids,
        request_id=getattr(request.state, "request_id", None),
    )
    return ok(result.as_dict())


@router.get("/{meta_id}/members")
def list_members(meta_id: UUID, db: DbSession, ws: CurrentWorkspace):
    meta = _get_part(db, ws.id, meta_id, include_archived=True)
    rows = db.query(PartMetaMember).filter_by(
        workspace_id=ws.id,
        meta_part_id=meta.id,
    ).all()
    return ok([{"id": str(r.id), "member_part_id": str(r.part_id)} for r in rows])


@router.post("/{meta_id}/members", status_code=status.HTTP_201_CREATED)
def add_member(
    request: Request,
    meta_id: UUID,
    payload: MetaMemberIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    meta = _get_part(db, ws.id, meta_id)
    if meta.part_type != "meta":
        raise_http(400, code=ErrorCodes.PART_NOT_META, message="part is not a meta-part")
    member = _get_part(db, ws.id, payload.member_part_id)
    if member.id == meta.id:
        raise_http(
            400,
            code=ErrorCodes.PART_META_SELF_MEMBER,
            message="meta-part cannot include itself",
        )
    if member.part_type == "meta":
        raise_http(
            400,
            code=ErrorCodes.PART_META_MEMBER_META,
            message="meta-part members cannot themselves be meta",
        )
    existing = db.query(PartMetaMember).filter_by(
        workspace_id=ws.id,
        meta_part_id=meta.id,
        part_id=member.id,
    ).first()
    if existing:
        return ok({"id": str(existing.id), "member_part_id": str(existing.part_id)})
    row = PartMetaMember(workspace_id=ws.id, meta_part_id=meta.id, part_id=member.id)
    db.add(row)
    db.flush()
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="part.member_added",
        target_type="part_meta_member",
        target_ids=[meta.id, member.id],
        comment="relation=meta_member",
        request_id=getattr(request.state, "request_id", None),
    )
    return ok({"id": str(row.id), "member_part_id": str(row.part_id)})


@router.delete("/{meta_id}/members/{member_id}")
def del_member(
    request: Request,
    meta_id: UUID,
    member_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    # Removal is allowed even on archived meta-parts.
    meta = _get_part(db, ws.id, meta_id, include_archived=True)
    deleted = db.query(PartMetaMember).filter_by(
        workspace_id=ws.id,
        meta_part_id=meta.id,
        part_id=member_id,
    ).delete()
    if deleted:
        _audit_log(
            db,
            ws=ws,
            user=user,
            action="part.member_removed",
            target_type="part_meta_member",
            target_ids=[meta.id, member_id],
            comment="relation=meta_member",
            request_id=getattr(request.state, "request_id", None),
        )
    return ok(None, "deleted")
