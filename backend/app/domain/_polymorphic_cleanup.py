"""Polymorphic-table orphan cleanup helper.

The cross-cutting tables — attachments, custom_fields, tag_links,
object_codes — point at their parent through a (type, id) pair with no FK
on the id column. When a parent row is hard-deleted (rare; most deletes
are soft-archive) the child rows become orphans. This helper purges them
cleanly while honouring the CLAUDE.md hard invariant: every DELETE must
filter by workspace_id.

The first three name their columns `object_type` / `object_id`;
`object_codes` names them `entity_type` / `entity_id` (its discriminator
is a closed CHECK-constrained set, not a free-form string, so it reads as
an entity rather than an object). `_CHILD_TABLES` carries the column
names per table so the purge is one code path either way.

Usage::

    from app.domain._polymorphic_cleanup import purge_polymorphic

    counts = purge_polymorphic(
        db,
        workspace_id=ws.id,
        object_type="part",
        object_id=part.id,
    )
    # counts == {"attachments": N, "custom_fields": M,
    #            "tag_links": K, "object_codes": J}

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
from app.domain.codes.models import ObjectCode
from app.domain.custom_fields.models import CustomField
from app.domain.tags.models import TagLink

_log = logging.getLogger(__name__)

_CleanupExecutor = Session | Connection
# (Model, table name, discriminator column, parent-id column)
_CleanupModel = tuple[object, str, str, str]


def _child_tables() -> tuple[_CleanupModel, ...]:
    return (
        (Attachment, "attachments", "object_type", "object_id"),
        (CustomField, "custom_fields", "object_type", "object_id"),
        (TagLink, "tag_links", "object_type", "object_id"),
        (ObjectCode, "object_codes", "entity_type", "entity_id"),
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

    for Model, table_name, type_col, id_col in _child_tables():
        result = executor.execute(
            delete(Model).where(
                Model.workspace_id == workspace_id,
                getattr(Model, type_col) == object_type,
                getattr(Model, id_col) == object_id,
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
        "workspace_id=%s %s",
        object_type,
        object_id,
        workspace_id,
        " ".join(f"{table}={count}" for table, count in counts.items()),
    )


def register_polymorphic_cleanup_listeners() -> None:
    """Register idempotent before_delete listeners for polymorphic parents."""
    for Model in polymorphic_parent_models().values():
        if not event.contains(Model, "before_delete", _purge_polymorphic_on_delete):
            event.listen(Model, "before_delete", _purge_polymorphic_on_delete)
