"""Part-category CRUD.

Every function is workspace-scoped: reads filter on `ws.id`, lookups go
through `assert_in_workspace` so a foreign UUID is a 404 rather than a
cross-tenant read (ADR-0002). Writes `db.flush()` — the `get_db`
dependency owns the commit.
"""
from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api._helpers import assert_in_workspace
from app.core.errors import ErrorCodes, raise_http
from app.core.time import utcnow
from app.domain.categories.models import PartCategory
from app.domain.categories.schemas import PartCategoryIn, PartCategoryPatch

UQ_PART_CATEGORIES_WS_NAME = "uq_part_categories_ws_name"
UQ_PART_CATEGORIES_WS_SLUG = "uq_part_categories_ws_slug"

# `library_slug` is String(60) — truncate before the trailing-dash strip so
# a cut mid-word can't leave the slug ending in a separator.
_SLUG_MAX_LENGTH = 60
_SLUG_FALLBACK = "category"
_NON_SLUG_RUN = re.compile(r"[^a-z0-9]+")

# Columns a PATCH may not blank out — they're NOT NULL in the schema, so a
# `null` for one of them means "leave alone", not "clear".
_NON_NULLABLE_PATCH_FIELDS = frozenset({"name", "sort_order", "library_slug"})


def slugify(value: str) -> str:
    """Derive a URL- and KiCad-library-safe slug from free text.

    Lower-cases, collapses every run of non-alphanumerics to one dash,
    trims dashes off both ends, and truncates to the column width.
    Returns `"category"` when nothing usable survives (e.g. a name made
    entirely of punctuation or non-Latin script).
    """
    slug = _NON_SLUG_RUN.sub("-", value.strip().lower()).strip("-")
    return slug[:_SLUG_MAX_LENGTH].strip("-") or _SLUG_FALLBACK


def _active_by(
    db: Session,
    *,
    ws: Any,
    column,
    value: str,
    exclude_id: UUID | None = None,
) -> PartCategory | None:
    stmt: Select = (
        select(PartCategory)
        .where(PartCategory.workspace_id == ws.id)
        .where(PartCategory.archived_at.is_(None))
        .where(column == value)
    )
    if exclude_id is not None:
        stmt = stmt.where(PartCategory.id != exclude_id)
    return db.execute(stmt.limit(1)).scalars().first()


def _raise_conflict(existing: PartCategory, *, code: str, message: str) -> None:
    raise_http(
        status.HTTP_409_CONFLICT,
        code=code,
        message=message,
        existing_id=str(existing.id),
        existing_name=existing.name,
    )


def _assert_available(
    db: Session,
    *,
    ws: Any,
    name: str,
    library_slug: str,
    exclude_id: UUID | None = None,
) -> None:
    """Pre-flight the two partial unique indexes so the caller gets a 409
    naming the row it collided with, instead of a bare IntegrityError."""
    clash = _active_by(
        db, ws=ws, column=PartCategory.name, value=name, exclude_id=exclude_id
    )
    if clash is not None:
        _raise_conflict(
            clash,
            code=ErrorCodes.CATEGORY_NAME_CONFLICT,
            message=f'category "{name}" already exists',
        )
    clash = _active_by(
        db, ws=ws, column=PartCategory.library_slug, value=library_slug, exclude_id=exclude_id
    )
    if clash is not None:
        _raise_conflict(
            clash,
            code=ErrorCodes.CATEGORY_SLUG_CONFLICT,
            message=f'library slug "{library_slug}" is already used by "{clash.name}"',
        )


def _raise_for_unique_violation(exc: IntegrityError, *, name: str, library_slug: str) -> None:
    """Map a lost race on either partial unique index onto the same 409 the
    pre-check would have produced. Two concurrent creates both pass
    `_assert_available` and only one reaches COMMIT; without this the loser
    gets a 500."""
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    constraint = getattr(diag, "constraint_name", None)
    if constraint == UQ_PART_CATEGORIES_WS_NAME:
        raise_http(
            status.HTTP_409_CONFLICT,
            code=ErrorCodes.CATEGORY_NAME_CONFLICT,
            message=f'category "{name}" already exists',
        )
    if constraint == UQ_PART_CATEGORIES_WS_SLUG:
        raise_http(
            status.HTTP_409_CONFLICT,
            code=ErrorCodes.CATEGORY_SLUG_CONFLICT,
            message=f'library slug "{library_slug}" is already in use',
        )


def list_categories(
    db: Session,
    *,
    ws: Any,
    include_archived: bool = False,
    limit: int = 200,
) -> list[PartCategory]:
    stmt = select(PartCategory).where(PartCategory.workspace_id == ws.id)
    if not include_archived:
        stmt = stmt.where(PartCategory.archived_at.is_(None))
    stmt = stmt.order_by(PartCategory.sort_order.asc(), PartCategory.name.asc())
    stmt = stmt.limit(limit)
    return list(db.execute(stmt).scalars())


def get_category(db: Session, *, ws: Any, category_id: UUID) -> PartCategory:
    """Fetch one category, archived included (so the restore path still
    resolves). 404 on miss *or* cross-workspace — re-coded to the
    domain-specific `category.not_found` the way `_parts_shared.get_part`
    does, so the frontend can switch on it."""
    try:
        return assert_in_workspace(db, PartCategory, category_id, ws.id, label="category")
    except HTTPException as exc:
        # Only re-code the 404; if assert_in_workspace ever grows another
        # status (403/400), let it through untouched instead of silently
        # rewriting it into a not-found.
        if exc.status_code != status.HTTP_404_NOT_FOUND:
            raise
        raise_http(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCodes.CATEGORY_NOT_FOUND,
            message="category not found",
        )


def create_category(
    db: Session,
    *,
    ws: Any,
    user_id: UUID | None,
    payload: PartCategoryIn,
) -> PartCategory:
    library_slug = payload.library_slug or slugify(payload.name)
    _assert_available(db, ws=ws, name=payload.name, library_slug=library_slug)

    category = PartCategory(
        workspace_id=ws.id,
        name=payload.name,
        description=payload.description,
        sort_order=payload.sort_order,
        refdes_prefix=payload.refdes_prefix,
        default_symbol_ref=payload.default_symbol_ref,
        default_footprint_ref=payload.default_footprint_ref,
        footprint_filters=payload.footprint_filters,
        library_slug=library_slug,
        created_by=user_id,
        updated_by=user_id,
    )
    try:
        with db.begin_nested():
            db.add(category)
            db.flush()
    except IntegrityError as exc:
        _raise_for_unique_violation(exc, name=payload.name, library_slug=library_slug)
        raise
    return category


def update_category(
    db: Session,
    *,
    ws: Any,
    category_id: UUID,
    user_id: UUID | None,
    payload: PartCategoryPatch,
) -> PartCategory:
    category = get_category(db, ws=ws, category_id=category_id)
    data = payload.model_dump(exclude_unset=True)
    # These columns are NOT NULL, so an explicit null can't mean anything —
    # reject it honestly instead of silently dropping it (a silent no-op
    # would return 200 while ignoring what the caller asked for).
    for field in _NON_NULLABLE_PATCH_FIELDS:
        if field in data and data[field] is None:
            raise_http(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                code=ErrorCodes.CATEGORY_FIELD_NOT_NULLABLE,
                message=f"{field} cannot be null",
            )

    # Renaming deliberately does NOT re-derive `library_slug`: it's the
    # stable identifier a KiCad library nickname is built from, so it only
    # moves when the caller asks for it explicitly.
    name = data.get("name", category.name)
    library_slug = data.get("library_slug", category.library_slug)
    if category.archived_at is None:
        _assert_available(
            db, ws=ws, name=name, library_slug=library_slug, exclude_id=category.id
        )

    for key, value in data.items():
        setattr(category, key, value)
    category.updated_by = user_id
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:
        _raise_for_unique_violation(exc, name=name, library_slug=library_slug)
        raise
    return category


def archive_category(
    db: Session,
    *,
    ws: Any,
    category_id: UUID,
    user_id: UUID | None,
) -> PartCategory:
    category = get_category(db, ws=ws, category_id=category_id)
    if category.archived_at is None:
        category.archived_at = utcnow()
        category.updated_by = user_id
        db.flush()
    return category


def restore_category(
    db: Session,
    *,
    ws: Any,
    category_id: UUID,
    user_id: UUID | None,
) -> PartCategory:
    """Un-archive. The name and slug were freed on archive, so another row
    may have taken them in the meantime — that's a 409, not a silent
    IntegrityError at commit time."""
    category = get_category(db, ws=ws, category_id=category_id)
    if category.archived_at is None:
        return category
    _assert_available(
        db,
        ws=ws,
        name=category.name,
        library_slug=category.library_slug,
        exclude_id=category.id,
    )
    category.archived_at = None
    category.updated_by = user_id
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:
        _raise_for_unique_violation(
            exc, name=category.name, library_slug=category.library_slug
        )
        raise
    return category
