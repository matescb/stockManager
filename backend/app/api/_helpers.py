from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select


def assert_in_workspace(
    db,
    Model: Any,
    id_: UUID,
    workspace_id: UUID,
    *,
    label: str | None = None,
) -> Any:
    """Look up a workspace-owned row by id, scoped to the current workspace.

    Returns the row on success; raises 404 if it does not exist *or* belongs
    to a different workspace. This is the only correct way to validate an
    ID accepted from a request body / path / query — `db.get(Model, id)`
    cannot express the workspace filter and lets a foreign-workspace UUID
    through to writes that reference it.
    """
    row = db.execute(
        select(Model).where(Model.id == id_, Model.workspace_id == workspace_id)
    ).scalar_one_or_none()
    if row is None:
        name = label or getattr(Model, "__tablename__", "row")
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{name} not found")
    return row
