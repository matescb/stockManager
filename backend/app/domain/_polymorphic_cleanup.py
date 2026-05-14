"""Polymorphic-table orphan cleanup helper.

The three cross-cutting tables — attachments, custom_fields, tag_links —
use a (object_type, object_id) pattern with no FK on object_id. When a
parent row is hard-deleted (rare; most deletes are soft-archive) the child
rows become orphans. This helper purges them cleanly while honouring the
CLAUDE.md hard invariant: every DELETE must filter by workspace_id.

Usage::

    from app.domain._polymorphic_cleanup import purge_polymorphic

    counts = purge_polymorphic(
        db,
        workspace_id=ws.id,
        object_type="part",
        object_id=part.id,
    )
    # counts == {"attachments": N, "custom_fields": M, "tag_links": K}

See docs/ARCHITECTURE.md — Polymorphic tables contract.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import delete, event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapper, Session, object_session

from app.domain.attachments.models import Attachment
from app.domain.custom_fields.models import CustomField
from app.domain.tags.models import TagLink

_log = logging.getLogger(__name__)

_CleanupExecutor = Session | Connection
_CleanupModel = tuple[object, str]


def _child_tables() -> tuple[_CleanupModel, ...]:
    return (
        (Attachment, "attachments"),
        (CustomField, "custom_fields"),
        (TagLink, "tag_links"),
    )


def polymorphic_parent_models() -> Mapping[str, type]:
    """Object types whose hard-deletes must purge polymorphic children."""
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


def _object_type_for_parent(target: object) -> str | None:
    target_type = type(target)
    for object_type, Model in polymorphic_parent_models().items():
        if target_type is Model:
            return object_type
    return None


def _purge_polymorphic(
    executor: _CleanupExecutor,
    *,
    workspace_id: UUID,
    object_type: str,
    object_id: UUID,
) -> dict[str, int]:
    counts: dict[str, int] = {}

    for Model, table_name in _child_tables():
        result = executor.execute(
            delete(Model).where(
                Model.workspace_id == workspace_id,
                Model.object_type == object_type,
                Model.object_id == object_id,
            )
        )
        counts[table_name] = int(result.rowcount or 0)

    return counts


def purge_polymorphic(
    db: Session,
    *,
    workspace_id: UUID,
    object_type: str,
    object_id: UUID,
) -> dict[str, int]:
    """Delete all polymorphic child rows for a given object.

    Filters by workspace_id on every DELETE — required by the workspace-
    isolation invariant (CLAUDE.md). Returns a dict of deleted-row counts
    keyed by table name so callers can log observability data.

    This is intentionally a bulk DELETE, not a per-row ORM fetch, so it
    stays fast even for objects with hundreds of attachments/fields/links.
    """
    return _purge_polymorphic(
        db,
        workspace_id=workspace_id,
        object_type=object_type,
        object_id=object_id,
    )


def _purge_polymorphic_on_delete(
    _mapper: Mapper,
    connection: Connection,
    target: object,
) -> None:
    object_type = _object_type_for_parent(target)
    if object_type is None:
        return

    workspace_id = getattr(target, "workspace_id", None)
    object_id = getattr(target, "id", None)
    if workspace_id is None or object_id is None:
        return

    session = object_session(target)
    if session is not None:
        seen = session.info.setdefault("polymorphic_cleanup_seen", set())
        key = (object_type, object_id, id(target))
        if key in seen:
            return
        seen.add(key)

    counts = _purge_polymorphic(
        connection,
        workspace_id=workspace_id,
        object_type=object_type,
        object_id=object_id,
    )
    _log.info(
        "polymorphic_cleanup hard_delete object_type=%s object_id=%s "
        "workspace_id=%s attachments=%d custom_fields=%d tag_links=%d",
        object_type,
        object_id,
        workspace_id,
        counts["attachments"],
        counts["custom_fields"],
        counts["tag_links"],
    )


def register_polymorphic_cleanup_listeners() -> None:
    """Register idempotent before_delete listeners for polymorphic parents."""
    for Model in polymorphic_parent_models().values():
        if not event.contains(Model, "before_delete", _purge_polymorphic_on_delete):
            event.listen(Model, "before_delete", _purge_polymorphic_on_delete)
