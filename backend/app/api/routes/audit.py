"""Read-only audit log endpoint (BE2-024).

GET /api/audit — returns the most recent audit rows for the current
workspace.  Gated on ``require_role("admin")`` so regular members and
viewers cannot enumerate past admin actions.

Cursor pagination via ``before_id`` (UUID of the last row the client
already has) so the client can page back in time without an offset that
drifts as new rows are inserted.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.core.deps import CurrentWorkspace, DbSession, require_role
from app.core.responses import ok
from app.domain.audit.models import AuditLog
from app.domain.audit.schemas import AuditLogOut

router = APIRouter()


@router.get("", dependencies=[Depends(require_role("admin"))])
def list_audit_log(
    db: DbSession,
    ws: CurrentWorkspace,
    limit: int = Query(default=50, ge=1, le=200),
    before_id: UUID | None = Query(default=None),
) -> dict:
    """Return audit rows for this workspace in reverse-chronological order.

    Workspace isolation: the query always filters by ``ws.id`` so an
    admin in workspace A can never read workspace B's log even if they
    somehow know a valid ``before_id`` from it.

    Cursor pagination: supply ``before_id`` (the id of the oldest row
    you already have) to fetch the next page.  The cursor is the row's
    ``created_at`` + ``id`` pair — we look up the pivot row first, then
    filter rows older than it.  Because ``created_at`` has microsecond
    resolution and ``id`` is UUID (random), duplicates are rare; the
    ``id`` tie-break makes the pagination stable even if two rows share
    the same timestamp.
    """
    stmt = (
        select(AuditLog)
        .where(AuditLog.workspace_id == ws.id)
    )

    if before_id is not None:
        pivot = db.get(AuditLog, before_id)
        # Silently ignore an unknown / cross-workspace before_id — the
        # client may have a stale cursor from a deleted row.  Isolation
        # is enforced by the workspace_id check.
        if pivot and pivot.workspace_id == ws.id:
            stmt = stmt.where(
                (AuditLog.created_at < pivot.created_at)
                | (
                    (AuditLog.created_at == pivot.created_at)
                    & (AuditLog.id < pivot.id)
                )
            )

    stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)
    rows = list(db.execute(stmt).scalars())

    return ok([AuditLogOut.model_validate(r).model_dump(mode="json") for r in rows])
