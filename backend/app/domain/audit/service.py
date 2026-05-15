"""Audit-log service (BE2-024).

Single entry point: ``log()``. Callers pass the workspace, the acting
user, the action string, and an optional list of affected object IDs.
Auth/system events that have no workspace context may pass ``ws=None``.
The row is flushed (not committed) so it rides the route's own
transaction — if the route rolls back, the audit row disappears too,
which is the correct behaviour.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.audit.models import AuditLog
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace


def log(
    db: Session,
    *,
    ws: Workspace | None,
    user: User | None,
    action: str,
    target_type: str | None = None,
    target_ids: list[UUID] | None = None,
    comment: str | None = None,
    request_id: str | None = None,
) -> AuditLog:
    """Append one audit row to the current DB session.

    The row is flushed immediately so its ``id`` is available to the
    caller, but is not committed until the route's ``get_db`` dependency
    commits at clean exit.  A rollback in the route will also roll back
    this row — there is no separate audit-only transaction.

    ``ws`` is required for workspace-scoped events. Pass ``None`` only
    for auth/system events where no workspace membership exists; the
    workspace audit API filters by workspace_id, so those rows remain
    outside tenant-scoped audit views.

    Never store decrypted credentials here (invariant from CLAUDE.md /
    BE2-024): callers that log credential rotation MUST NOT pass the
    key material as ``comment``.
    """
    row = AuditLog(
        workspace_id=ws.id if ws else None,
        user_id=user.id if user else None,
        action=action,
        target_type=target_type,
        target_ids=target_ids or None,
        comment=comment,
        request_id=request_id,
    )
    db.add(row)
    db.flush()
    return row
