"""User-domain service helpers.

Currently scoped to ownership-deletion preconditions (DB-013 / issue
#104). When a `DELETE /api/users/{id}` endpoint eventually lands, it
must call :func:`assert_user_deletable` before issuing the SQL ``DELETE``;
``workspaces.owner_user_id`` carries ``ondelete='RESTRICT'`` so a raw
delete of a workspace owner would otherwise bubble a Postgres
``ForeignKeyViolation`` into a generic 500. The guard surfaces a clean
409 with the list of owned workspaces so the caller (UI or admin tool)
can prompt for ownership reassignment.

See ``docs/ARCHITECTURE.md`` (Future work) for the operational
runbook.
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.domain.workspaces.models import Workspace


def assert_user_deletable(db: Session, user_id: uuid.UUID) -> None:
    """Raise 409 if the user owns one or more workspaces.

    The ``RESTRICT`` FK on ``workspaces.owner_user_id`` is the database
    safety net; this guard is the user-friendly layer on top. The
    response surfaces the owned workspace ids/names so the caller can
    either reassign ownership or hard-delete the workspaces first.

    The check covers *every* workspace the user owns regardless of any
    future ``archived_at`` flag: the FK fires on archived rows just the
    same. If/when soft-archive lands on workspaces, this helper does
    not need to change.
    """
    owned = (
        db.query(Workspace.id, Workspace.name)
        .filter(Workspace.owner_user_id == user_id)
        .order_by(Workspace.created_at.asc())
        .all()
    )
    if not owned:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "message": "user owns workspaces",
            "code": "owns_workspaces",
            "workspaces": [
                {"id": str(ws_id), "name": ws_name} for ws_id, ws_name in owned
            ],
        },
    )
