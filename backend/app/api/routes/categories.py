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
from app.domain.parts.services import spec_columns

router = APIRouter()

# `list_columns` / `list_sort` name canonical spec keys, and which keys are
# legal depends on the category (`domain/parts/services/spec_columns.py`).
# Pydantic fixes the SHAPE; the vocabulary check has to happen here,
# against the row being written, so a key the listing would render as a
# permanently blank column is a 422 instead.
_LIST_SETTING_FIELDS = frozenset({"list_columns", "list_sort"})


def _validate_list_settings(payload, schema) -> None:
    if payload.list_columns:
        spec_columns.validated_keys(payload.list_columns, schema)
    if payload.list_sort is not None:
        spec_columns.validated_keys([payload.list_sort.key], schema)


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
    if _LIST_SETTING_FIELDS & payload.model_fields_set:
        _validate_list_settings(
            payload,
            spec_columns.prospective_schema(
                db, ws=ws, name=payload.name, parent_id=payload.parent_id
            ),
        )
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
    if _LIST_SETTING_FIELDS & payload.model_fields_set:
        # Before the write, and against the row as it stands: a bad key must
        # not land in the JSONB column and then need a second PATCH to
        # clear. `get_category` is the 404-on-foreign-workspace gate the
        # update path would apply anyway.
        _validate_list_settings(
            payload,
            spec_columns.effective_schema(
                db,
                ws=ws,
                category=categories_service.get_category(
                    db, ws=ws, category_id=category_id
                ),
            ),
        )
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


@router.get("/{category_id}/spec-schema")
def category_spec_schema(
    category_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
) -> Envelope[dict]:
    """Which canonical spec keys this category's parts have, and which of
    them the parts list is configured to show.

    The `slug` is resolved by walking the category's name path up the tree
    (`spec_columns.effective_schema`), so a leaf *Ceramic* under
    *Capacitors* answers with the `capacitor_ceramic` schema while a bare
    root *Capacitors* answers with the common keys only — a capacitor with
    no dielectric named genuinely has no schema of its own.

    `list_columns` / `list_sort` are the STORED choice resolved through the
    same tree, and `inherited_from` / `sort_inherited_from` name the
    ancestor each came from (null when this category owns it). Read-only —
    writing them is a PATCH on the category.
    """
    category = categories_service.get_category(db, ws=ws, category_id=category_id)
    return ok(
        spec_columns.serialize_schema(
            spec_columns.effective_schema(db, ws=ws, category=category)
        )
    )
