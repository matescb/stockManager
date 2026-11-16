"""Box intake + box audit endpoints (phase 15), under /api/storage/{id}.

POST /{storage_id}/scan/resolve — what a scanned bag / typed MPN refers to
POST /{storage_id}/intake       — put one bag into the box (own lot)
GET  /{storage_id}/bags         — the box's recorded contents, per bag
POST /{storage_id}/audit        — apply an audit's verdicts in one transaction

The logic lives in `domain/stock/box_scan.py`; this module only resolves
the box, wires the workspace's primary provider in, maps errors and writes
the audit_log row.
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy.exc import DBAPIError

from app.api._helpers import assert_in_workspace
from app.api.routes._stock_integrity import raise_integrity_as_409
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.errors import ErrorCodes, raise_http
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import ok
from app.core.secrets import decrypt
from app.domain.audit.service import log as _audit_log
from app.domain.parts.providers import ProviderUpstreamError, make_provider
from app.domain.parts.services.provider_cache import lookup_with_cache
from app.domain.stock import box_scan
from app.domain.stock.schemas import BoxAuditIn, BoxIntakeIn, BoxScanResolveIn
from app.domain.stock.service import StockConflictError, StockError
from app.domain.storage.models import StorageLocation

router = APIRouter()
logger = logging.getLogger(__name__)


def _box(db, ws_id, storage_id: UUID) -> StorageLocation:
    try:
        return assert_in_workspace(db, StorageLocation, storage_id, ws_id, label="storage")
    except HTTPException:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCodes.STORAGE_NOT_FOUND,
            message="storage not found",
        )


def _provider(ws):
    """(lookup callable, provider name), or (None, None) with no provider."""
    provider = make_provider(
        ws.parts_provider,
        decrypt(ws.parts_provider_api_key),
        decrypt(ws.parts_provider_api_secret),
    )
    if provider is None:
        return None, None

    def lookup(mpn: str) -> dict:
        try:
            return lookup_with_cache(provider, mpn.strip())
        except ProviderUpstreamError as exc:
            logger.warning("box scan: provider lookup failed for %s: %s", mpn, exc.message)
            return {"found": False, "result": None, "message": exc.message}

    return lookup, provider.name


def _raise_box_error(exc: box_scan.BoxScanError):
    raise_http(exc.status, code=exc.code, message=str(exc), **exc.extra)


def _raise_stock_error(exc: StockError):
    if isinstance(exc, StockConflictError):
        raise_http(
            status.HTTP_409_CONFLICT,
            code=ErrorCodes.STOCK_CONSTRAINT_VIOLATION,
            message=str(exc),
            constraint=exc.constraint,
            storage_location_id=str(exc.storage_location_id),
        )
    raise_http(status.HTTP_400_BAD_REQUEST, code=ErrorCodes.STOCK_OPERATION_ERROR, message=str(exc))


@router.post("/{storage_id}/scan/resolve")
@limiter.limit("120/minute", key_func=workspace_key)
def resolve_scan(
    request: Request,
    storage_id: UUID,
    payload: BoxScanResolveIn,
    db: DbSession,
    ws: CurrentWorkspace,
):
    box = _box(db, ws.id, storage_id)
    lookup, _name = _provider(ws)
    try:
        return ok(box_scan.resolve_scan(
            db, workspace_id=ws.id, storage=box, payload=payload, lookup=lookup,
        ))
    except box_scan.BoxScanError as exc:
        _raise_box_error(exc)


@router.post("/{storage_id}/intake")
@limiter.limit("120/minute", key_func=workspace_key)
def intake(
    request: Request,
    storage_id: UUID,
    payload: BoxIntakeIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    box = _box(db, ws.id, storage_id)
    lookup, provider_name = _provider(ws)
    try:
        result = box_scan.intake_bag(
            db, workspace_id=ws.id, user_id=user.id, storage=box, payload=payload,
            lookup=lookup, provider_name=provider_name,
        )
    except box_scan.BoxScanError as exc:
        _raise_box_error(exc)
    except StockError as exc:
        _raise_stock_error(exc)
    except DBAPIError as exc:
        raise_integrity_as_409(exc)
    _audit_log(
        db,
        ws=ws,
        user=user,
        action=f"storage.intake.{result['status']}",
        target_type="storage_location",
        target_ids=[box.id, UUID(result["part"]["id"]), UUID(result["lot_id"])],
        comment=f"qty {result['quantity']}" + (", part created" if result["part_created"] else ""),
    )
    return ok(result)


@router.get("/{storage_id}/bags")
def bags(storage_id: UUID, db: DbSession, ws: CurrentWorkspace):
    box = _box(db, ws.id, storage_id)
    return ok(box_scan.box_bags(db, workspace_id=ws.id, storage_location_id=box.id))


@router.post("/{storage_id}/audit")
def audit(
    storage_id: UUID,
    payload: BoxAuditIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    box = _box(db, ws.id, storage_id)
    for line in payload.lines:
        if line.to_storage_location_id is not None:
            _box(db, ws.id, line.to_storage_location_id)
    try:
        counts = box_scan.apply_audit(
            db, workspace_id=ws.id, user_id=user.id, storage=box, payload=payload,
        )
    except box_scan.BoxScanError as exc:
        _raise_box_error(exc)
    except StockError as exc:
        _raise_stock_error(exc)
    except DBAPIError as exc:
        raise_integrity_as_409(exc)
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="storage.audited",
        target_type="storage_location",
        target_ids=[box.id],
        comment=", ".join(f"{k} {v}" for k, v in counts.items()),
    )
    return ok(counts)
