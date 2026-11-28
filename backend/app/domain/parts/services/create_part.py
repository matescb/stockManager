"""Creating a part, for every door that can create one.

Lifted out of `api/routes/parts_core.py::create_part` when the MCP
surface grew a `create_part` tool. The two callers need the same six
rules and must not drift on any of them:

* **at least one of `name` / `mpn`.** `name` is optional so an operator
  can paste an MPN and go; with neither there is nothing to call the row.
* **the name defaults to the MPN**, after stripping. Both values are
  stripped first, so `"  "` is blank rather than a name made of spaces.
* **MPN uniqueness is pre-checked** so the conflict can NAME the part
  already holding it (`existing_id` / `existing_name`), which a bare
  `IntegrityError` from `uq_parts_ws_mpn` cannot. The index is still the
  authority — a lost race re-reads and raises the same 409, so the two
  paths give one answer.
* **caller-supplied FKs are workspace-checked.** `category_id` and
  `default_storage_location_id` arrive from the request body; without
  `assert_in_workspace` a caller in workspace B can persist a foreign
  UUID, which is both an existence oracle and a foot-gun for every
  downstream lookup (CLAUDE.md: isolation is enforced in code).
* **an archived category is refused**, because every picker hides them,
  so one arriving here can only come from a stale or hand-built request.
* **the workspace part policy's `when=create` rules are enforced** — a
  422 `part.policy_violation` with the structured `missing` list. Last,
  after the MPN check, so an MPN that already exists is still reported
  as the existing part (which the MCP tool turns into a success) rather
  than as a policy failure of a part nobody needs to create.
* **a part with an MPN is linked or refused** — after the row exists,
  `link_on_create.link_new_part` links it to every distributor with an
  exact match, or raises 422 `part.not_available_from_providers` / 503
  when a distributor could not be asked (owner's rule, 2026-11-28).

What this does NOT do is write the audit row or commit. Both belong to
the caller: the route has a `request_id` off `request.state` and the MCP
tool has one off the principal, and `get_db` / `unit_of_work` own the
transaction boundary (BE2-010) — a commit here would split it and let
partial state outlive a later raise.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api._helpers import assert_in_workspace
from app.core.errors import ErrorCodes, raise_http
from app.domain.categories.models import PartCategory
from app.domain.part_policy.service import enforce_create as _enforce_policy
from app.domain.parts.models import Part
from app.domain.parts.schemas import PartIn
from app.domain.parts.services.mpn_unique import (
    active_part_by_mpn,
    is_mpn_unique_violation,
)
from app.domain.storage.models import StorageLocation

__all__ = ["check_references", "create_part", "raise_mpn_conflict"]


def raise_mpn_conflict(existing: Part) -> None:
    """409 naming the part that already holds this MPN.

    The shape the create-part client reads `existing_id` /
    `existing_name` from, and the shape the MCP tool turns back into a
    "found it" success.
    """
    raise_http(
        status.HTTP_409_CONFLICT,
        code=ErrorCodes.PART_MPN_CONFLICT,
        message=f'MPN already used by part "{existing.name}"',
        existing_id=str(existing.id),
        existing_name=existing.name,
    )


def check_references(db: Session, *, ws: Any, payload: PartIn) -> None:
    """Workspace-check the caller-supplied FKs; refuse an archived category.

    Split out so the dry run (`POST /api/parts/validate`, MCP
    `validate_part`) refuses exactly what the create would — a validate
    that passes followed by a create that fails is the one outcome the
    dry run exists to prevent.
    """
    if payload.default_storage_location_id is not None:
        assert_in_workspace(
            db,
            StorageLocation,
            payload.default_storage_location_id,
            ws.id,
            label="storage location",
        )

    if payload.category_id is not None:
        category = assert_in_workspace(
            db, PartCategory, payload.category_id, ws.id, label="category"
        )
        if category.archived_at is not None:
            raise_http(409, ErrorCodes.CATEGORY_ARCHIVED, "Category is archived")


def create_part(
    db: Session,
    *,
    ws: Any,
    user_id: UUID | None,
    payload: PartIn,
) -> Part:
    """Create one part in `ws`, or raise the 409 naming the MPN's owner."""
    name = (payload.name or "").strip()
    mpn = (payload.mpn or "").strip()
    if not name and not mpn:
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=ErrorCodes.PART_NAME_OR_MPN_REQUIRED,
            message="provide at least one of `name` or `mpn`",
        )
    if not name:
        name = mpn

    if mpn:
        existing = active_part_by_mpn(db, workspace_id=ws.id, mpn=mpn)
        if existing:
            raise_mpn_conflict(existing)

    check_references(db, ws=ws, payload=payload)
    _enforce_policy(db, ws=ws, payload=payload)

    part = Part(
        workspace_id=ws.id,
        part_type=payload.part_type,
        name=name,
        manufacturer=payload.manufacturer,
        mpn=mpn or None,
        internal_part_number=payload.internal_part_number,
        description=payload.description,
        notes_markdown=payload.notes_markdown,
        footprint=payload.footprint,
        low_stock_report_quantity=payload.low_stock_report_quantity,
        attrition_percentage=payload.attrition_percentage,
        attrition_min_quantity=payload.attrition_min_quantity,
        default_storage_location_id=payload.default_storage_location_id,
        default_storage_mandatory=payload.default_storage_mandatory,
        serialized=payload.serialized,
        category_id=payload.category_id,
        created_by=user_id,
        updated_by=user_id,
    )
    try:
        with db.begin_nested():
            db.add(part)
            db.flush()
    except IntegrityError as exc:
        if mpn and is_mpn_unique_violation(exc):
            existing = active_part_by_mpn(db, workspace_id=ws.id, mpn=mpn)
            if existing is not None:
                raise_mpn_conflict(existing)
        raise
    # A part with an MPN is linked to the distributors that sell it, or
    # not created (owner's rule; `link_on_create`). Imported here: the
    # refresh service imports this package's siblings at module level.
    from app.domain.parts.services.link_on_create import link_new_part

    link_new_part(db, ws=ws, part=part, user_id=user_id)
    return part
