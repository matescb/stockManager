"""Part-category CRUD — `/api/categories`.

Thin routes: every query and conflict check lives in
`app/domain/categories/service.py`. Writes are member-gated by
`_member_gate` in `main.py` and rate-limited per workspace.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Request, status

from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import Envelope, ok
from app.domain.audit.service import log as _audit_log
from app.domain.categories import service as categories_service
from app.domain.categories.schemas import (
    PartCategoryIn,
    PartCategoryOut,
    PartCategoryPatch,
)

router = APIRouter()


@router.get("")
def list_categories(
    db: DbSession,
    ws: CurrentWorkspace,
    include_archived: bool = Query(default=False),
    limit: int = Query(default=200, le=1000),
) -> Envelope[list[PartCategoryOut]]:
    rows = categories_service.list_categories(
        db, ws=ws, include_archived=include_archived, limit=limit
    )
    return ok([PartCategoryOut.model_validate(row) for row in rows])


@router.post("", status_code=status.HTTP_201_CREATED)
@limiter.limit("30/minute", key_func=workspace_key)
def create_category(
    request: Request,
    payload: PartCategoryIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[PartCategoryOut]:
    category = categories_service.create_category(db, ws=ws, user_id=user.id, payload=payload)
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="category.created",
        target_type="part_category",
        target_ids=[category.id],
        comment="fields=" + ",".join(sorted(payload.model_fields_set)),
        request_id=getattr(request.state, "request_id", None),
    )
    return ok(PartCategoryOut.model_validate(category))


@router.patch("/{category_id}")
@limiter.limit("30/minute", key_func=workspace_key)
def patch_category(
    request: Request,
    category_id: UUID,
    payload: PartCategoryPatch,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[PartCategoryOut]:
    category = categories_service.update_category(
        db, ws=ws, category_id=category_id, user_id=user.id, payload=payload
    )
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="category.updated",
        target_type="part_category",
        target_ids=[category.id],
        comment="fields=" + ",".join(sorted(payload.model_fields_set)),
        request_id=getattr(request.state, "request_id", None),
    )
    return ok(PartCategoryOut.model_validate(category))


@router.post("/{category_id}/archive")
@limiter.limit("30/minute", key_func=workspace_key)
def archive_category(
    request: Request,
    category_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[None]:
    """Archive. Any direct subcategories are promoted to the root of the
    tree — the same thing the `ON DELETE SET NULL` FK does on a hard
    delete. See `service.archive_category`."""
    category, promoted = categories_service.archive_category(
        db, ws=ws, category_id=category_id, user_id=user.id
    )
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="category.archived",
        target_type="part_category",
        target_ids=[category.id],
        comment=f"promoted_children={promoted}" if promoted else None,
        request_id=getattr(request.state, "request_id", None),
    )
    return ok(None, "archived")


@router.post("/{category_id}/restore")
@limiter.limit("30/minute", key_func=workspace_key)
def restore_category(
    request: Request,
    category_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[None]:
    """Un-archive. 409 when the freed name or slug has since been claimed
    by another active category — see `service.restore_category`."""
    category = categories_service.restore_category(
        db, ws=ws, category_id=category_id, user_id=user.id
    )
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="category.restored",
        target_type="part_category",
        target_ids=[category.id],
        request_id=getattr(request.state, "request_id", None),
    )
    return ok(None, "restored")
