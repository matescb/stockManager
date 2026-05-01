from __future__ import annotations

from functools import lru_cache
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


# Allow-list of (object_type → SQLAlchemy model) for the polymorphic
# (object_type, object_id) tables: attachments, custom_fields, tag_links.
# Adding a new entry here is the safe way to let a new resource accept
# attachments / custom fields / tag links — without it, callers cannot
# reference the new object type and the cross-tenant write guard remains
# load-bearing.
@lru_cache(maxsize=1)
def _polymorphic_resolvers() -> dict[str, Any]:
    # Lazy + cached: keeps the API layer from creating a hard import-time
    # dependency on the parts domain, and avoids rebuilding the dict on
    # every attachment upload / custom-field write / tag link. Adding a
    # new entry requires a process restart — that's fine, this list is
    # static at deploy time.
    from app.domain.parts.models import Part

    return {
        "part": Part,
    }


def assert_polymorphic_in_workspace(
    db,
    object_type: str,
    object_id: UUID,
    workspace_id: UUID,
) -> Any:
    """Resolve a polymorphic (object_type, object_id) reference against the
    current workspace.

    Raises 400 on unknown object_type, 404 on not-found-or-cross-workspace.
    The cross-tenant write guard on attachments / custom_fields / tag_links
    is load-bearing — a missing call here lets a caller in workspace B
    write rows tagged with B's workspace_id but pointing at an object_id
    owned by workspace A.
    """
    resolvers = _polymorphic_resolvers()
    Model = resolvers.get(object_type)
    if Model is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"unknown object_type: {object_type}",
        )
    return assert_in_workspace(db, Model, object_id, workspace_id, label=object_type)
