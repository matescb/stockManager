from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.api._helpers import assert_in_workspace, assert_polymorphic_in_workspace
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.responses import ok
from app.domain.tags.models import Tag, TagLink
from app.domain.tags.schemas import TagIn, TagLinkIn

router = APIRouter()


@router.get("")
def list_tags(
    db: DbSession,
    ws: CurrentWorkspace,
    limit: int = Query(default=200, le=1000),
):
    rows = list(
        db.execute(
            select(Tag)
            .where(Tag.workspace_id == ws.id)
            .order_by(Tag.name)
            .limit(limit)
        ).scalars()
    )
    return ok([{"id": str(r.id), "name": r.name, "color": r.color} for r in rows])


@router.post("", status_code=status.HTTP_201_CREATED)
def create(payload: TagIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    t = Tag(workspace_id=ws.id, name=payload.name, color=payload.color, created_by=user.id, updated_by=user.id)
    db.add(t)
    db.flush()
    return ok({"id": str(t.id), "name": t.name, "color": t.color})


@router.post("/links", status_code=status.HTTP_201_CREATED)
def link(payload: TagLinkIn, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    tag = assert_in_workspace(db, Tag, payload.tag_id, ws.id, label="tag")
    # Polymorphic FK validation: object_id must name a row in the current
    # workspace, and object_type must be a known resource. Without this
    # guard a caller in workspace B can tag a part_id owned by workspace A.
    assert_polymorphic_in_workspace(db, payload.object_type, payload.object_id, ws.id)
    existing = (
        db.execute(
            select(TagLink)
            .where(TagLink.workspace_id == ws.id)
            .where(TagLink.tag_id == tag.id)
            .where(TagLink.object_type == payload.object_type)
            .where(TagLink.object_id == payload.object_id)
        )
        .scalars()
        .first()
    )
    if existing:
        return ok({"id": str(existing.id)})
    tl = TagLink(
        workspace_id=ws.id,
        tag_id=tag.id,
        object_type=payload.object_type,
        object_id=payload.object_id,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(tl)
    db.flush()
    return ok({"id": str(tl.id)})


@router.delete("/links/{link_id}")
def unlink(link_id: UUID, db: DbSession, ws: CurrentWorkspace):
    # Per the rest of the API (orders/projects/parts), DELETE on an id
    # that doesn't exist in this workspace returns 404 — never silently
    # succeed (would let a caller probe foreign-workspace ids by their
    # 200/404 split). Use the canonical helper.
    row = assert_in_workspace(db, TagLink, link_id, ws.id)
    db.delete(row)
    return ok(None, "deleted")


@router.get("/by-object/{object_type}/{object_id}")
def list_for_object(object_type: str, object_id: UUID, db: DbSession, ws: CurrentWorkspace):
    rows = list(
        db.execute(
            select(TagLink, Tag)
            .join(Tag, Tag.id == TagLink.tag_id)
            .where(TagLink.workspace_id == ws.id)
            .where(TagLink.object_type == object_type)
            .where(TagLink.object_id == object_id)
        )
    )
    return ok([{"id": str(tl.id), "tag": {"id": str(t.id), "name": t.name, "color": t.color}} for tl, t in rows])
