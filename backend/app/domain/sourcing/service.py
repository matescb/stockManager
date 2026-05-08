"""Service facade for TrustedParts sourcing."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.domain.sourcing import cache
from app.domain.sourcing.budget import BUDGET
from app.domain.sourcing.factory import make_sourcing_provider
from app.domain.sourcing.schemas import (
    SourcingAttributionLinks,
    SourcingQuery,
    SourcingSearchOut,
    SourcingSearchRaw,
    SourcingSearchResult,
)

TTL_SECONDS = 30 * 60
TRUSTEDPARTS_LINKS = SourcingAttributionLinks(
    primary="https://www.trustedparts.com/",
    attribution="https://www.trustedparts.com/en/about",
)


class SourcingNotConfigured(Exception):
    """Workspace has no usable TrustedParts sourcing configuration."""


class SourcingBudgetBlocked(Exception):
    """Workspace exceeded the hard TrustedParts parts-count budget."""


def dedupe_mpns(mpns: Iterable[str | None]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for mpn in mpns:
        if mpn is None:
            continue
        clean_mpn = mpn.strip()
        if not clean_mpn:
            continue
        key = clean_mpn.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(clean_mpn)
    return deduped


def chunk_mpns(mpns: Sequence[str], size: int = 50) -> list[list[str]]:
    if size < 1 or size > 50:
        raise ValueError("MPN chunk size must be between 1 and 50")
    return [list(mpns[index : index + size]) for index in range(0, len(mpns), size)]


def search(
    db: Session,
    *,
    workspace: Any,
    mpns: list[str],
    country: str | None = None,
    currency: str | None = None,
    in_stock_only: bool = False,
    distributors: list[str] | None = None,
    use_cached_data: bool | None = None,
    requested_by: UUID | None = None,
) -> SourcingSearchOut:
    clean_mpns = [mpn.strip() for mpn in mpns]
    if not 1 <= len(clean_mpns) <= 50 or any(not mpn for mpn in clean_mpns):
        raise ValueError("sourcing search requires 1 to 50 non-empty MPNs")

    provider = make_sourcing_provider(workspace)
    if provider is None:
        raise SourcingNotConfigured("sourcing not configured")

    effective_country = _clean_code(country) or workspace.sourcing_country_code
    effective_currency = _clean_code(currency) or workspace.sourcing_currency_code
    effective_distributors = (
        _clean_distributors(distributors)
        if distributors is not None
        else _clean_distributors(workspace.sourcing_preferred_distributors)
    )
    effective_use_cached = (
        bool(workspace.sourcing_use_cached_for_dashboards)
        if use_cached_data is None
        else use_cached_data
    )

    verdict = BUDGET.check(workspace.id, parts_count=len(clean_mpns))
    if not verdict.allow:
        raise SourcingBudgetBlocked(verdict.reason)
    if verdict.mode == "degraded":
        effective_use_cached = True

    provider.country_code = effective_country
    provider.currency_code = effective_currency

    results: list[SourcingSearchResult] = []
    for mpn in clean_mpns:
        query = _canonical_query(
            mpn=mpn,
            country=effective_country,
            currency=effective_currency,
            in_stock_only=in_stock_only,
            distributors=effective_distributors,
            use_cached_data=effective_use_cached,
        )

        def fetch() -> dict[str, Any]:
            fetched_at = utcnow()
            raw = provider.search(
                [SourcingQuery(search_token=mpn)],
                exact_match=True,
                in_stock_only=in_stock_only,
                distributors=effective_distributors,
                use_cached_data=effective_use_cached,
            )
            return {
                "offers": [offer.model_dump(mode="json") for offer in raw.offers],
                "request_id": raw.request_id,
                "fetched_at": fetched_at.isoformat(),
            }

        response, cache_hit = cache.get_or_fetch(
            db,
            workspace_id=workspace.id,
            query=query,
            ttl_seconds=TTL_SECONDS,
            fetch_fn=fetch,
            created_by=requested_by,
        )
        if not cache_hit:
            BUDGET.record(workspace.id, 1)

        raw = _raw_from_cache_response(response)
        fetched_at = _fetched_at_from_response(response)
        results.append(
            SourcingSearchResult(
                mpn=mpn,
                offers=raw.offers,
                request_id=raw.request_id,
                fetched_at=fetched_at,
                cache_hit=cache_hit,
            )
        )

    response_fetched_at = max((result.fetched_at for result in results), default=utcnow())
    request_id = next((result.request_id for result in results if result.request_id), None)
    return SourcingSearchOut(
        results=results,
        request_id=request_id,
        fetched_at=response_fetched_at,
        cache_hit=all(result.cache_hit for result in results),
        links=TRUSTEDPARTS_LINKS,
    )


def _canonical_query(
    *,
    mpn: str,
    country: str | None,
    currency: str | None,
    in_stock_only: bool,
    distributors: list[str] | None,
    use_cached_data: bool,
) -> dict[str, Any]:
    return {
        "provider": "trustedparts",
        "mpn": mpn,
        "country": country,
        "currency": currency,
        "in_stock_only": in_stock_only,
        "distributors": distributors or [],
        "use_cached_data": use_cached_data,
        "exact_match": True,
    }


def _raw_from_cache_response(response: dict[str, Any]) -> SourcingSearchRaw:
    return SourcingSearchRaw.model_validate(
        {
            "offers": response.get("offers", []),
            "request_id": response.get("request_id"),
        }
    )


def _fetched_at_from_response(response: dict[str, Any]) -> datetime:
    fetched_at = response.get("fetched_at")
    if isinstance(fetched_at, str):
        return datetime.fromisoformat(fetched_at)
    return utcnow()


def _clean_code(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().upper()
    return value or None


def _clean_distributors(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    distributors = [str(item).strip() for item in value if str(item).strip()]
    return distributors or None
