"""Read-only report services."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.domain.lots.models import Lot
from app.domain.parts.models import Part
from app.domain.reports.schemas import (
    SourcingRiskReportOut,
    SourcingRiskRow,
    SourcingRiskStatusOut,
)
from app.domain.sourcing import (
    SourcingAuthError,
    SourcingClientError,
    SourcingRateLimitError,
    SourcingTimeoutError,
)
from app.domain.sourcing import service as sourcing_service
from app.domain.sourcing.schemas import SourcingBomOfferOut, SourcingSearchResult
from app.domain.sourcing.service import SourcingBudgetBlocked, SourcingNotConfigured
from app.domain.stock.service import bulk_current_quantities

SOURCING_RISK_TTL_SECONDS = 4 * 60 * 60
PRICE_DELTA_THRESHOLD = Decimal("0.25")
LEAD_TIME_LONG_DAYS = 30
MOQ_OVERBUY_MULTIPLIER = 5


def sourcing_risk_report(
    db: Session,
    *,
    workspace: Any,
    only_with_flags: bool = True,
    use_cached_data: bool | None = None,
    requested_by: UUID | None = None,
) -> SourcingRiskReportOut:
    parts = list(
        db.execute(
            select(Part)
            .where(Part.workspace_id == workspace.id)
            .where(Part.archived_at.is_(None))
            .where(Part.mpn.is_not(None))
            .order_by(Part.name)
        ).scalars()
    )
    parts = [part for part in parts if (part.mpn or "").strip()]
    part_ids = [part.id for part in parts]
    on_hand = bulk_current_quantities(
        db,
        workspace_id=workspace.id,
        part_ids=part_ids,
        status="on_hand",
    )
    historical = _historical_unit_costs(db, workspace_id=workspace.id, part_ids=part_ids)

    mpns = sourcing_service.dedupe_mpns(part.mpn for part in parts)
    search_results: dict[str, SourcingSearchResult] = {}
    fetched_at_values: list[datetime] = []
    partial = False
    cache_hit_values: list[bool] = []
    effective_use_cached = True if use_cached_data is None else use_cached_data
    sourcing_status = SourcingRiskStatusOut(state="ok", message="OK")

    if mpns:
        try:
            for chunk in sourcing_service.chunk_mpns(mpns):
                out = sourcing_service.search(
                    db,
                    workspace=workspace,
                    mpns=chunk,
                    distributors=[],
                    use_cached_data=effective_use_cached,
                    ttl_seconds=SOURCING_RISK_TTL_SECONDS,
                    requested_by=requested_by,
                )
                fetched_at_values.append(out.fetched_at)
                cache_hit_values.append(out.cache_hit)
                for result in out.results:
                    search_results[result.mpn.casefold()] = result
        except SourcingNotConfigured:
            sourcing_status = SourcingRiskStatusOut(
                state="not_configured",
                message="TrustedParts sourcing is not configured",
            )
        except SourcingBudgetBlocked:
            partial = True
            sourcing_status = SourcingRiskStatusOut(
                state="budget_blocked",
                message="TrustedParts sourcing budget exhausted",
            )
        except (
            SourcingAuthError,
            SourcingRateLimitError,
            SourcingTimeoutError,
            SourcingClientError,
        ):
            partial = True
            sourcing_status = SourcingRiskStatusOut(
                state="upstream_error",
                message="TrustedParts sourcing request failed",
            )

    preferred = _clean_distributors(workspace.sourcing_preferred_distributors)
    rows = [
        _sourcing_risk_row(
            part,
            result=search_results.get((part.mpn or "").casefold()),
            on_hand=on_hand.get(part.id, 0),
            historical=historical.get(part.id),
            preferred_distributors=preferred,
        )
        for part in parts
    ]
    if only_with_flags:
        rows = [row for row in rows if row.risk_flags]
    rows.sort(key=lambda row: (-len(row.risk_flags), row.name.casefold(), row.mpn.casefold()))

    return SourcingRiskReportOut(
        rows=rows,
        sourcing_status=sourcing_status,
        fetched_at=max(fetched_at_values, default=utcnow()),
        partial=partial,
        cache_hit=all(cache_hit_values) if cache_hit_values else None,
        links=sourcing_service.TRUSTEDPARTS_LINKS,
    )


def _sourcing_risk_row(
    part: Part,
    *,
    result: SourcingSearchResult | None,
    on_hand: int,
    historical: tuple[Decimal, str | None] | None,
    preferred_distributors: list[str],
) -> SourcingRiskRow:
    mpn = (part.mpn or "").strip()
    typical_qty = max(int(part.low_stock_report_quantity or 10), 10)
    offers = sourcing_service._joined_offers(  # noqa: SLF001
        [mpn],
        {mpn.casefold(): result} if result is not None else {},
        qty=typical_qty,
    )
    best_offer = _best_stocked_offer(offers, typical_qty)
    distributors_with_stock = _distributors_with_stock(offers)
    authorized_stock = sum(max(0, offer.stock) for offer in offers)
    historical_unit_cost, historical_currency = historical or (None, None)
    price_delta_pct = _price_delta_pct(best_offer, historical_unit_cost, historical_currency)

    flags: list[str] = []
    if len(distributors_with_stock) == 1:
        flags.append("single_source")
    if not distributors_with_stock and on_hand > 0:
        flags.append("no_authorized_stock")
    if (
        best_offer is not None
        and best_offer.moq is not None
        and best_offer.moq > typical_qty * MOQ_OVERBUY_MULTIPLIER
    ):
        flags.append("moq_overbuy")
    if (
        best_offer is not None
        and best_offer.lead_time_days is not None
        and best_offer.lead_time_days > LEAD_TIME_LONG_DAYS
    ):
        flags.append("lead_time_long")
    if preferred_distributors and not (
        {item.casefold() for item in preferred_distributors}
        & {item.casefold() for item in distributors_with_stock}
    ):
        flags.append("preferred_distributor_unmet")
    if price_delta_pct is not None and price_delta_pct >= PRICE_DELTA_THRESHOLD:
        flags.append("price_delta")

    return SourcingRiskRow(
        part_id=part.id,
        name=part.name,
        manufacturer=part.manufacturer,
        mpn=mpn,
        on_hand=on_hand,
        distributors_with_stock=distributors_with_stock,
        authorized_stock=authorized_stock,
        best_offer=best_offer,
        lead_time_days=best_offer.lead_time_days if best_offer is not None else None,
        typical_reorder_quantity=typical_qty,
        historical_unit_cost=historical_unit_cost,
        historical_currency=historical_currency,
        price_delta_pct=price_delta_pct,
        risk_flags=flags,
    )


def _best_stocked_offer(
    offers: Sequence[SourcingBomOfferOut],
    qty: int,
) -> SourcingBomOfferOut | None:
    stocked = [offer for offer in offers if offer.stock > 0]
    if stocked:
        return sourcing_service._best_offer_at_qty(list(stocked), qty)  # noqa: SLF001
    return sourcing_service._best_offer_at_qty(list(offers), qty)  # noqa: SLF001


def _distributors_with_stock(offers: Sequence[SourcingBomOfferOut]) -> list[str]:
    by_key: dict[str, str] = {}
    stock_by_key: dict[str, int] = {}
    for offer in offers:
        key = offer.distributor.casefold()
        by_key.setdefault(key, offer.distributor)
        stock_by_key[key] = stock_by_key.get(key, 0) + max(0, offer.stock)
    return sorted(
        [by_key[key] for key, stock in stock_by_key.items() if stock > 0],
        key=str.casefold,
    )


def _historical_unit_costs(
    db: Session,
    *,
    workspace_id: UUID,
    part_ids: list[UUID],
) -> dict[UUID, tuple[Decimal, str | None]]:
    if not part_ids:
        return {}
    rows = db.execute(
        select(Lot)
        .where(Lot.workspace_id == workspace_id)
        .where(Lot.part_id.in_(part_ids))
        .where(Lot.purchase_unit_cost.is_not(None))
        .order_by(Lot.part_id, Lot.created_at.desc())
    ).scalars()
    out: dict[UUID, tuple[Decimal, str | None]] = {}
    for lot in rows:
        if lot.part_id not in out and lot.purchase_unit_cost is not None:
            out[lot.part_id] = (Decimal(lot.purchase_unit_cost), lot.purchase_currency)
    return out


def _price_delta_pct(
    best_offer: SourcingBomOfferOut | None,
    historical_unit_cost: Decimal | None,
    historical_currency: str | None,
) -> Decimal | None:
    if (
        best_offer is None
        or best_offer.unit_price is None
        or historical_unit_cost is None
        or historical_unit_cost <= 0
    ):
        return None
    if historical_currency and best_offer.currency and historical_currency != best_offer.currency:
        return None
    return (best_offer.unit_price - historical_unit_cost) / historical_unit_cost


def _clean_distributors(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
