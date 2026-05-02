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

from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.domain.attachments.models import Attachment
from app.domain.custom_fields.models import CustomField
from app.domain.tags.models import TagLink


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
    counts: dict[str, int] = {}

    result = db.execute(
        delete(Attachment).where(
            Attachment.workspace_id == workspace_id,
            Attachment.object_type == object_type,
            Attachment.object_id == object_id,
        )
    )
    counts["attachments"] = result.rowcount

    result = db.execute(
        delete(CustomField).where(
            CustomField.workspace_id == workspace_id,
            CustomField.object_type == object_type,
            CustomField.object_id == object_id,
        )
    )
    counts["custom_fields"] = result.rowcount

    result = db.execute(
        delete(TagLink).where(
            TagLink.workspace_id == workspace_id,
            TagLink.object_type == object_type,
            TagLink.object_id == object_id,
        )
    )
    counts["tag_links"] = result.rowcount

    return counts
