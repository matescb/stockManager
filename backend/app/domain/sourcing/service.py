"""Service facade for TrustedParts sourcing."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.domain.builds.service import shortage_analysis
from app.domain.parts.models import Part
from app.domain.sourcing import cache
from app.domain.sourcing.budget import BUDGET
from app.domain.sourcing.coverage import compute_build_capacity, compute_coverage
from app.domain.sourcing.factory import make_sourcing_provider
from app.domain.sourcing.pricing import best_unit_price_at_qty
from app.domain.sourcing.schemas import (
    BuildCapacityOut,
    DistributorCoverageMatrixOut,
    SourcingAttributionLinks,
    SourcingBomLineOut,
    SourcingBomOfferOut,
    SourcingBomOut,
    SourcingBomPriceBreakOut,
    SourcingQuery,
    SourcingSearchOut,
    SourcingSearchRaw,
    SourcingSearchResult,
)

TTL_SECONDS = 30 * 60
BOM_TTL_SECONDS = 10 * 60
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


def source_bom(
    db: Session,
    *,
    workspace: Any,
    project: Any,
    build_quantity: int,
    country: str | None = None,
    currency: str | None = None,
    distributors: list[str] | None = None,
    in_stock_only: bool = False,
    use_cached_data: bool | None = None,
    ttl_seconds: int = BOM_TTL_SECONDS,
    requested_by: UUID | None = None,
) -> SourcingBomOut:
    shortage = shortage_analysis(
        db,
        workspace_id=workspace.id,
        project=project,
        build_quantity=build_quantity,
    )
    part_ids = _part_ids_from_shortage(shortage)
    parts_by_id = _parts_by_id(db, workspace_id=workspace.id, part_ids=part_ids)
    mpns = dedupe_mpns(
        parts_by_id[part_id].mpn
        for part_id in part_ids
        if part_id in parts_by_id
    )

    search_results: dict[str, SourcingSearchResult] = {}
    fetched_at_values: list[datetime] = []
    partial = False
    for chunk in chunk_mpns(mpns):
        verdict = BUDGET.check(workspace.id, parts_count=len(chunk))
        partial = partial or verdict.mode == "degraded"
        out = search(
            db,
            workspace=workspace,
            mpns=chunk,
            country=country,
            currency=currency,
            in_stock_only=in_stock_only,
            distributors=distributors,
            use_cached_data=use_cached_data,
            ttl_seconds=ttl_seconds,
            requested_by=requested_by,
        )
        fetched_at_values.append(out.fetched_at)
        for result in out.results:
            search_results[result.mpn.casefold()] = result

    preferred = _clean_distributors(workspace.sourcing_preferred_distributors)
    rows = [
        _source_bom_line(
            row,
            parts_by_id=parts_by_id,
            search_results=search_results,
            preferred_distributors=preferred,
        )
        for row in shortage
    ]
    return SourcingBomOut(
        rows=rows,
        coverage=DistributorCoverageMatrixOut.model_validate(
            compute_coverage(rows, preferred_distributors=preferred)
        ),
        capacity=BuildCapacityOut.model_validate(
            compute_build_capacity(
                rows,
                requested_build_quantity=build_quantity,
            )
        ),
        fetched_at=max(fetched_at_values, default=utcnow()),
        partial=partial,
        links=TRUSTEDPARTS_LINKS,
    )


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
    ttl_seconds: int = TTL_SECONDS,
    requested_by: UUID | None = None,
    force_refresh: bool = False,
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
    if verdict.mode == "degraded" and not force_refresh:
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
            ttl_seconds=ttl_seconds,
            fetch_fn=fetch,
            created_by=requested_by,
            force_refresh=force_refresh,
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


def _part_ids_from_shortage(shortage: list[dict[str, Any]]) -> list[UUID]:
    out: list[UUID] = []
    for row in shortage:
        raw_ids = [row.get("part_id"), *row.get("substitute_ids", [])]
        for raw_id in raw_ids:
            if raw_id is None:
                continue
            part_id = UUID(str(raw_id))
            if part_id not in out:
                out.append(part_id)
    return out


def _parts_by_id(
    db: Session,
    *,
    workspace_id: UUID,
    part_ids: list[UUID],
) -> dict[UUID, Part]:
    if not part_ids:
        return {}
    parts = db.execute(
        select(Part).where(Part.workspace_id == workspace_id, Part.id.in_(part_ids))
    ).scalars()
    return {part.id: part for part in parts}


def _source_bom_line(
    row: dict[str, Any],
    *,
    parts_by_id: dict[UUID, Part],
    search_results: dict[str, SourcingSearchResult],
    preferred_distributors: list[str] | None,
) -> SourcingBomLineOut:
    part_id = UUID(str(row["part_id"]))
    substitute_ids = [UUID(str(item)) for item in row.get("substitute_ids", [])]
    candidate_ids = [part_id, *substitute_ids]
    candidate_mpns = dedupe_mpns(
        parts_by_id[item].mpn for item in candidate_ids if item in parts_by_id
    )
    short_by = int(row["short_by"])
    offers = _joined_offers(candidate_mpns, search_results, qty=max(short_by, 1))
    best_offer = _best_offer_at_qty(offers, short_by)
    authorized_stock = sum(offer.stock for offer in offers)

    return SourcingBomLineOut(
        project_entry_id=UUID(str(row["project_entry_id"])),
        part_id=part_id,
        part_name=str(row["part_name"]),
        mpn=parts_by_id[part_id].mpn if part_id in parts_by_id else None,
        required=int(row["required"]),
        available=int(row["available"]),
        substitute_ids=substitute_ids,
        substitute_available=int(row.get("substitute_available", 0)),
        short_by=short_by,
        authorized_stock=authorized_stock,
        offers=offers,
        best_offer=best_offer,
        est_extended_cost=(
            best_offer.unit_price * Decimal(short_by)
            if best_offer is not None and best_offer.unit_price is not None
            else None
        ),
        lead_time_days=best_offer.lead_time_days if best_offer is not None else None,
        risk_flags=_risk_flags(
            offers,
            best_offer=best_offer,
            short_by=short_by,
            preferred_distributors=preferred_distributors,
        ),
    )


def _joined_offers(
    mpns: list[str],
    search_results: dict[str, SourcingSearchResult],
    *,
    qty: int,
) -> list[SourcingBomOfferOut]:
    out: list[SourcingBomOfferOut] = []
    for mpn in mpns:
        result = search_results.get(mpn.casefold())
        if result is None:
            continue
        for offer in result.offers:
            offer_mpn = offer.mpn or mpn
            for distributor in offer.distributors:
                out.append(
                    SourcingBomOfferOut(
                        mpn=offer_mpn,
                        distributor=distributor.name,
                        sku=distributor.sku,
                        stock=max(0, int(distributor.stock or 0)),
                        unit_price=_unit_price_for_distributor(distributor, qty),
                        currency=distributor.currency,
                        packaging=distributor.packaging,
                        moq=distributor.moq,
                        lead_time_days=distributor.lead_time_days,
                        price_breaks=_price_breaks_for_distributor(distributor),
                        url=distributor.product_url or offer.links.primary,
                    )
                )
    return out


def _best_offer_at_qty(
    offers: list[SourcingBomOfferOut],
    qty: int,
) -> SourcingBomOfferOut | None:
    if qty < 1:
        return None

    best: tuple[Decimal, SourcingBomOfferOut] | None = None
    for offer in offers:
        price = offer.unit_price
        if price is None:
            continue
        if best is None or price < best[0]:
            best = (price, offer)
    return best[1] if best is not None else None


def _unit_price_for_distributor(distributor: Any, qty: int) -> Decimal | None:
    price_breaks = list(distributor.price_breaks)
    if not price_breaks and distributor.unit_price is not None:
        price_breaks = [
            {
                "quantity": max(1, int(distributor.moq or 1)),
                "unit_price": distributor.unit_price,
            }
        ]
    best = best_unit_price_at_qty(price_breaks, qty)
    return best[0] if best is not None else None


def _price_breaks_for_distributor(distributor: Any) -> list[SourcingBomPriceBreakOut]:
    price_breaks = list(distributor.price_breaks)
    if not price_breaks and distributor.unit_price is not None:
        price_breaks = [
            {
                "quantity": max(1, int(distributor.moq or 1)),
                "unit_price": distributor.unit_price,
            }
        ]
    return [
        SourcingBomPriceBreakOut(quantity=quantity, unit_price=unit_price)
        for item in price_breaks
        if (best := best_unit_price_at_qty([item], 1)) is not None
        for unit_price, quantity in [best]
    ]


def _risk_flags(
    offers: list[SourcingBomOfferOut],
    *,
    best_offer: SourcingBomOfferOut | None,
    short_by: int,
    preferred_distributors: list[str] | None,
) -> list[str]:
    stock_by_distributor: dict[str, int] = {}
    for offer in offers:
        key = offer.distributor.casefold()
        stock_by_distributor[key] = stock_by_distributor.get(key, 0) + offer.stock

    stocked_distributors = {
        distributor for distributor, stock in stock_by_distributor.items() if stock > 0
    }
    flags: list[str] = []
    if len(stocked_distributors) == 1:
        flags.append("single_source")
    if not stocked_distributors:
        flags.append("no_authorized_stock")
    if (
        best_offer is not None
        and best_offer.moq is not None
        and short_by > 0
        and best_offer.moq > short_by * 3
    ):
        flags.append("moq_overbuy")
    if (
        best_offer is not None
        and best_offer.lead_time_days is not None
        and best_offer.lead_time_days > 30
    ):
        flags.append("lead_time_long")
    if preferred_distributors:
        preferred = {item.casefold() for item in preferred_distributors}
        if not (preferred & stocked_distributors):
            flags.append("preferred_distributor_unmet")
    return flags


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
