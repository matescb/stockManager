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
from app.domain.sourcing.models import PurchasePlan, SourcingCache
from app.domain.workspaces.master_lists import ALL_DISTRIBUTORS

SEVEN_DAYS = timedelta(days=7)


def canonical_query_hash(query: dict[str, Any]) -> str:
    """SHA-256 of canonical JSON: sorted keys and compact separators."""
    canonical = json.dumps(query, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sourcing_search_query(
    *,
    workspace_id: UUID,
    provider: str,
    mpn: str,
    country_code: str | None,
    currency_code: str | None,
    language_code: str | None,
    distributors: list[str] | None,
    in_stock_only: bool,
    use_cached_data: bool,
    exact_match: bool = True,
) -> dict[str, Any]:
    """Canonical TrustedParts cache query shape.

    Fields are: ``workspace_id``, ``provider``, ``mpn``, ``country_code``,
    ``currency_code``, ``language_code``, sorted/canonical-cased
    ``distributors``, ``in_stock_only``, ``use_cached_data``, and
    ``exact_match``. Every field is an upstream request input or an isolation
    boundary that must produce a different cache hash when changed.
    """
    return {
        "workspace_id": str(workspace_id),
        "provider": provider.strip().casefold(),
        "mpn": mpn.strip(),
        "country_code": _canonical_code(country_code),
        "currency_code": _canonical_code(currency_code),
        "language_code": _canonical_language(language_code),
        "distributors": _canonical_distributors(distributors),
        "in_stock_only": in_stock_only,
        "use_cached_data": use_cached_data,
        "exact_match": exact_match,
    }


def purge_provider_cache(
    db: Session,
    *,
    workspace_id: UUID,
    provider: str,
) -> int:
    """Delete cache rows for one workspace/provider and return row count."""
    result = db.execute(
        delete(SourcingCache)
        .where(SourcingCache.workspace_id == workspace_id)
        .where(SourcingCache.query_json["provider"].astext == provider.strip().casefold())
    )
    return result.rowcount or 0


def get_or_fetch(
    db: Session,
    *,
    workspace_id: UUID,
    query: dict[str, Any],
    ttl_seconds: int,
    fetch_fn: Callable[[], dict[str, Any]],
    created_by: UUID | None = None,
    force_refresh: bool = False,
) -> tuple[dict[str, Any], bool]:
    """Return ``(response_json, cache_hit)`` for one workspace-scoped query."""
    now = utcnow()
    query_hash = canonical_query_hash(query)
    if not force_refresh:
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


def _canonical_code(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().upper()
    return value or None


def _canonical_language(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().casefold()
    return value or None


def _canonical_distributors(value: list[str] | None) -> list[str]:
    if not value:
        return []
    known = {item.casefold(): item for item in ALL_DISTRIBUTORS}
    canonical = {
        known.get(str(item).strip().casefold(), str(item).strip())
        for item in value
        if str(item).strip()
    }
    return sorted(canonical, key=str.casefold)


def sweep_expired(db: Session, *, workspace_id: UUID) -> int:
    """Delete expired rows for one workspace and return the number removed."""
    result = db.execute(
        delete(SourcingCache)
        .where(SourcingCache.workspace_id == workspace_id)
        .where(SourcingCache.expires_at < utcnow())
    )
    return result.rowcount or 0


def sweep_expired_purchase_plans(db: Session, *, workspace_id: UUID) -> int:
    """Delete expired purchase plans for one workspace."""
    result = db.execute(
        delete(PurchasePlan)
        .where(PurchasePlan.workspace_id == workspace_id)
        .where(PurchasePlan.expires_at < utcnow())
    )
    return result.rowcount or 0


def sweep_expired_all_workspaces(db: Session) -> int:
    """Delete expired sourcing rows by iterating through workspace scopes."""
    cache_workspace_ids = (
        db.execute(
            select(SourcingCache.workspace_id)
            .where(SourcingCache.expires_at < utcnow())
            .distinct()
        )
        .scalars()
        .all()
    )
    plan_workspace_ids = (
        db.execute(
            select(PurchasePlan.workspace_id)
            .where(PurchasePlan.expires_at < utcnow())
            .distinct()
        )
        .scalars()
        .all()
    )
    workspace_ids = set(cache_workspace_ids) | set(plan_workspace_ids)
    return sum(
        sweep_expired(db, workspace_id=workspace_id)
        + sweep_expired_purchase_plans(db, workspace_id=workspace_id)
        for workspace_id in workspace_ids
    )
