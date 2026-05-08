"""Short-lived TrustedParts sourcing response cache."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.domain.sourcing.models import SourcingCache

SEVEN_DAYS = timedelta(days=7)


def canonical_query_hash(query: dict[str, Any]) -> str:
    """SHA-256 of canonical JSON: sorted keys and compact separators."""
    canonical = json.dumps(query, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_or_fetch(
    db: Session,
    *,
    workspace_id: UUID,
    query: dict[str, Any],
    ttl_seconds: int,
    fetch_fn: Callable[[], dict[str, Any]],
    created_by: UUID | None = None,
) -> tuple[dict[str, Any], bool]:
    """Return ``(response_json, cache_hit)`` for one workspace-scoped query."""
    now = utcnow()
    query_hash = canonical_query_hash(query)
    cached = db.execute(
        select(SourcingCache)
        .where(SourcingCache.workspace_id == workspace_id)
        .where(SourcingCache.query_hash == query_hash)
        .where(SourcingCache.expires_at > now)
    ).scalar_one_or_none()
    if cached is not None:
        return cached.response_json, True

    response = fetch_fn()
    ttl = min(timedelta(seconds=ttl_seconds), SEVEN_DAYS)
    fetched_at = utcnow()
    expires_at = fetched_at + ttl
    values = {
        "workspace_id": workspace_id,
        "query_hash": query_hash,
        "query_json": query,
        "response_json": response,
        "fetched_at": fetched_at,
        "expires_at": expires_at,
        "created_by": created_by,
    }
    db.execute(
        pg_insert(SourcingCache.__table__)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["workspace_id", "query_hash"],
            set_=values,
        )
    )
    return response, False


def sweep_expired(db: Session, *, workspace_id: UUID) -> int:
    """Delete expired rows for one workspace and return the number removed."""
    result = db.execute(
        delete(SourcingCache)
        .where(SourcingCache.workspace_id == workspace_id)
        .where(SourcingCache.expires_at < utcnow())
    )
    return result.rowcount or 0
