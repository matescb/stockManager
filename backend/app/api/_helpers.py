from __future__ import annotations

from functools import lru_cache
from typing import TypeVar
from uuid import UUID

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import _ROLE_RANK, _membership_role
from app.core.errors import ErrorCodes, raise_http
from app.domain._mixins import WorkspaceOwned
from app.domain._polymorphic_cleanup import register_polymorphic_cleanup_listeners

register_polymorphic_cleanup_listeners()

# Generic type variable bound to WorkspaceOwned. Replaces the previous
# `Any`-typed signatures so a typo (`Parts` instead of `Part`) is caught
# by a static type-checker — these helpers ARE the workspace-isolation
# contract, so the typing here is load-bearing for code-review.
T = TypeVar("T", bound=WorkspaceOwned)


def assert_in_workspace(
    db: Session,
    Model: type[T],
    id_: UUID,
    workspace_id: UUID,
    *,
    label: str | None = None,
) -> T:
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
        raise_http(
            status.HTTP_404_NOT_FOUND,
            ErrorCodes.RESOURCE_NOT_FOUND,
            f"{name} not found",
            resource=name,
        )
    return row


# Allow-list of (object_type → SQLAlchemy model) for the polymorphic
# (object_type, object_id) tables: attachments, custom_fields, tag_links.
# Adding a new entry here is the safe way to let a new resource accept
# attachments / custom fields / tag links — without it, callers cannot
# reference the new object type and the cross-tenant write guard remains
# load-bearing.
@lru_cache(maxsize=1)
def _polymorphic_resolvers() -> dict[str, type[WorkspaceOwned]]:
    # Lazy + cached: keeps the API layer from creating a hard import-time
    # dependency on the parts domain, and avoids rebuilding the dict on
    # every attachment upload / custom-field write / tag link. Adding a
    # new entry requires a process restart — that's fine, this list is
    # static at deploy time.
    from app.domain.builds.models import Build
    from app.domain.lots.models import Lot
    from app.domain.orders.models import Order
    from app.domain.parts.models import Part
    from app.domain.projects.models import Project
    from app.domain.storage.models import StorageLocation

    return {
        "build": Build,
        "lot": Lot,
        "order": Order,
        "part": Part,
        "project": Project,
        "storage_location": StorageLocation,
    }


def assert_polymorphic_in_workspace(
    db: Session,
    object_type: str,
    object_id: UUID,
    workspace_id: UUID,
) -> WorkspaceOwned:
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
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            ErrorCodes.RESOURCE_UNKNOWN_OBJECT_TYPE,
            f"unknown object_type: {object_type}",
            object_type=object_type,
        )
    return assert_in_workspace(db, Model, object_id, workspace_id, label=object_type)


def assert_child_in_parent(
    db: Session,
    Model: type[T],
    child_id: UUID,
    parent,
    *,
    parent_fk: str,
    label: str,
) -> T:
    """Look up a child row that must belong to both the given parent and the
    parent's workspace.

    A single query covers workspace isolation + parent-FK check so neither
    can be bypassed independently. Returns the row on success; raises 404 if
    the row does not exist, belongs to another workspace, or belongs to a
    different parent object.

    This is the canonical shape for nested-resource endpoints like
    PATCH /projects/{project_id}/entries/{entry_id} — the previous hand-rolled
    `db.get(Child, id) + manual workspace_id + parent_id` pattern let a
    caller in workspace B pass a foreign entry_id and have it silently
    matched against workspace A's parent (BE2-021).
    """
    row = db.execute(
        select(Model).where(
            Model.id == child_id,
            Model.workspace_id == parent.workspace_id,
            getattr(Model, parent_fk) == parent.id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            ErrorCodes.RESOURCE_NOT_FOUND,
            f"{label} not found",
            resource=label,
        )
    return row


def require_resource_access(
    db: Session,
    Model: type[T],
    id_: UUID,
    *,
    user,
    ws,
    role: str = "member",
    label: str | None = None,
) -> T:
    """Resolve a workspace-owned resource by id and gate on the caller's
    role — in the right order so the response status leaks no info.

    The order is the whole point (BE2-009):

      1. existence in the DB
      2. membership in the caller's current workspace
      3. role check for that workspace

    Failing (1) or (2) → 404. The caller is told nothing about whether
    the row exists; the response is identical for "no such id" and "id
    in another workspace". Failing (3) → 403: the row exists *in this
    workspace*, the caller just lacks the named role for it.

    The previous shape used `dependencies=[Depends(require_role("admin"))]`,
    which runs the role check BEFORE the per-resource lookup. A member
    probing an archive endpoint with a foreign workspace's UUID got 403
    — an oracle telling the prober "this UUID exists somewhere; you
    just lack the role". Resource-first + role-second closes that.

    Usage::

        @router.post("/{part_id}/archive")
        def archive(part_id: UUID, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
            p = require_resource_access(db, Part, part_id, ws=ws, user=user, role="admin")
            ...

    Generic over `Model` — works for Part / Project / Order / Build /
    StorageLocation. The 404 message uses `label` (or the model's
    tablename) so the client gets a recognisable string.
    """
    floor = _ROLE_RANK[role]
    name = label or getattr(Model, "__tablename__", "row")

    # 1) existence + 2) workspace match — both fold into a single 404.
    # We call `db.get` (unscoped) and follow with the workspace equality
    # check; the 404 branch covers both "no such row" and "row in
    # another workspace" with the same response so a foreign-id probe
    # can't distinguish them.
    row = db.get(Model, id_)
    if row is None or getattr(row, "workspace_id", None) != ws.id:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            ErrorCodes.RESOURCE_NOT_FOUND,
            f"{name} not found",
            resource=name,
        )
    # 3) role check on the (now-known-to-be-in-workspace) resource. 403
    # here is correct — it tells the caller "you exist in this
    # workspace, the row exists, but you lack `role`" without leaking
    # anything about other workspaces.
    rank = _ROLE_RANK.get(_membership_role(db, user, ws), 0)
    if rank < floor:
        raise_http(
            status.HTTP_403_FORBIDDEN,
            ErrorCodes.RESOURCE_INSUFFICIENT_ROLE,
            f"requires role {role}+",
            required_role=role,
        )
    return row
