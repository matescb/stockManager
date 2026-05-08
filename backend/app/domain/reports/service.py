"""Read-only report services."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.domain.builds.service import shortage_analysis
from app.domain.lots.models import Lot
from app.domain.parts.models import Part
from app.domain.projects.models import Project
from app.domain.reports.schemas import (
    BomBuyabilityReportOut,
    CurrencyAmountOut,
    ProjectBuyabilityRow,
    ReplenishmentCostReportOut,
    ReplenishmentCostRow,
    ReplenishmentCostSort,
    ReplenishmentCostStatusOut,
    ReplenishmentCostTotalOut,
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
from app.domain.sourcing.schemas import (
    SourcingBomOfferOut,
    SourcingReportData,
    SourcingSearchResult,
)
from app.domain.sourcing.service import SourcingBudgetBlocked, SourcingNotConfigured
from app.domain.stock.service import bulk_current_quantities, bulk_current_quantities_by_lot

LOW_STOCK_SOURCING_TTL_SECONDS = 4 * 3600
BOM_BUYABILITY_PROJECT_CAP = 50
BOM_BUYABILITY_TTL_SECONDS = 4 * 60 * 60
REPLENISHMENT_COST_TTL_SECONDS = 4 * 60 * 60
SOURCING_RISK_TTL_SECONDS = 4 * 60 * 60
PRICE_DELTA_THRESHOLD = Decimal("0.25")
LEAD_TIME_LONG_DAYS = 30
MOQ_OVERBUY_MULTIPLIER = 5
_MONEY_ZERO = Decimal("0")
_PCT_QUANT = Decimal("0.01")


def replenishment_cost_report(
    db: Session,
    *,
    workspace: Any,
    use_cached_data: bool | None = None,
    sort: ReplenishmentCostSort = "delta_pct",
) -> ReplenishmentCostReportOut:
    """Transient replacement-cost report.

    TrustedParts-derived prices are read through the short-lived sourcing
    cache and are never persisted outside that cache.
    """
    parts = list(
        db.execute(
            select(Part)
            .where(Part.workspace_id == workspace.id)
            .where(Part.archived_at.is_(None))
            .where(Part.mpn.is_not(None))
        ).scalars()
    )
    on_hand_by_part = bulk_current_quantities(
        db,
        workspace_id=workspace.id,
        part_ids=[part.id for part in parts],
        status="on_hand",
    )
    report_parts = [
        part
        for part in parts
        if on_hand_by_part.get(part.id, 0) > 0 and part.mpn and part.mpn.strip()
    ]
    if not report_parts:
        return ReplenishmentCostReportOut(
            rows=[],
            totals=[],
            sourcing_status=_status_ok(),
        )

    historical_by_part = _historical_costs_by_part(db, workspace_id=workspace.id)
    search_results, status = _search_replenishment_offers(
        db,
        workspace=workspace,
        mpns=[part.mpn or "" for part in report_parts],
        use_cached_data=use_cached_data,
    )

    rows = [
        _row_for_part(
            part,
            on_hand=on_hand_by_part.get(part.id, 0),
            historical_costs=historical_by_part.get(part.id, {}),
            search_result=search_results.get((part.mpn or "").casefold()),
            sourcing_status=status,
        )
        for part in report_parts
    ]
    rows.sort(key=_sort_key(sort))
    return ReplenishmentCostReportOut(
        rows=rows,
        totals=_totals(rows),
        sourcing_status=status,
    )


SourcingStatus = Literal["ok", "not_configured", "partial", "budget_blocked"]

_log = logging.getLogger(__name__)


def bom_buyability_report(
    db: Session,
    *,
    workspace: Any,
    build_quantity: int = 1,
    use_cached_data: bool | None = None,
) -> BomBuyabilityReportOut:
    """Workspace-wide per-project buildability scoreboard."""
    effective_cache = True if use_cached_data is None else use_cached_data
    projects = list(
        db.execute(
            select(Project)
            .where(Project.workspace_id == workspace.id)
            .where(Project.archived_at.is_(None))
            .order_by(Project.created_at.desc(), Project.id.desc())
            .limit(BOM_BUYABILITY_PROJECT_CAP + 1)
        ).scalars()
    )
    truncated = len(projects) > BOM_BUYABILITY_PROJECT_CAP
    projects = projects[:BOM_BUYABILITY_PROJECT_CAP]

    rows: list[ProjectBuyabilityRow] = []
    statuses: set[str] = set()
    for project in projects:
        try:
            sourced = sourcing_service.source_bom(
                db,
                workspace=workspace,
                project=project,
                build_quantity=build_quantity,
                use_cached_data=effective_cache,
                ttl_seconds=BOM_BUYABILITY_TTL_SECONDS,
            )
        except SourcingNotConfigured:
            statuses.add("not_configured")
            rows.append(
                _unsourced_row(
                    db,
                    workspace=workspace,
                    project=project,
                    build_quantity=build_quantity,
                    partial=True,
                )
            )
        except SourcingBudgetBlocked:
            statuses.add("budget_blocked")
            rows.append(
                _unsourced_row(
                    db,
                    workspace=workspace,
                    project=project,
                    build_quantity=build_quantity,
                    partial=True,
                )
            )
        except (
            SourcingAuthError,
            SourcingRateLimitError,
            SourcingTimeoutError,
            SourcingClientError,
        ):
            statuses.add("partial")
            rows.append(
                _unsourced_row(
                    db,
                    workspace=workspace,
                    project=project,
                    build_quantity=build_quantity,
                    partial=True,
                )
            )
        else:
            if sourced.partial:
                statuses.add("partial")
            capacity = sourced.capacity
            rows.append(
                ProjectBuyabilityRow(
                    project_id=project.id,
                    project_name=project.name,
                    build_quantity=build_quantity,
                    can_build_now=capacity.can_build_now,
                    can_build_after_purchase=capacity.can_build_after_purchase,
                    blocking_lines_count=(
                        len(capacity.blocking_lines_after_purchase)
                        if capacity.can_build_after_purchase < build_quantity
                        else 0
                    ),
                    est_purchase_cost=capacity.est_purchase_cost,
                    partial=sourced.partial,
                )
            )

    return BomBuyabilityReportOut(
        build_quantity=build_quantity,
        rows=rows,
        sourcing_status=_coalesce_status(statuses),
        truncated=truncated,
        project_cap=BOM_BUYABILITY_PROJECT_CAP,
        links=sourcing_service.TRUSTEDPARTS_LINKS,
    )


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


def _coalesce_status(statuses: set[str]) -> str:
    for status in ("budget_blocked", "not_configured", "partial"):
        if status in statuses:
            return status
    return "ok"


def _unsourced_row(
    db: Session,
    *,
    workspace: Any,
    project: Project,
    build_quantity: int,
    partial: bool,
) -> ProjectBuyabilityRow:
    rows = shortage_analysis(
        db,
        workspace_id=workspace.id,
        project=project,
        build_quantity=build_quantity,
    )
    can_build_now = _can_build_now(rows, build_quantity=build_quantity)
    return ProjectBuyabilityRow(
        project_id=project.id,
        project_name=project.name,
        build_quantity=build_quantity,
        can_build_now=can_build_now,
        can_build_after_purchase=can_build_now,
        blocking_lines_count=sum(1 for row in rows if int(row["short_by"]) > 0),
        est_purchase_cost=None,
        partial=partial,
    )


def _can_build_now(rows: list[dict[str, Any]], *, build_quantity: int) -> int:
    effective = [row for row in rows if int(row["required"]) > 0]
    if not effective:
        return 0
    return min(
        (int(row["available"]) + int(row.get("substitute_available", 0)))
        * build_quantity
        // int(row["required"])
        for row in effective
    )


def _historical_costs_by_part(
    db: Session,
    *,
    workspace_id: UUID,
) -> dict[UUID, dict[str | None, Decimal]]:
    lot_qty = bulk_current_quantities_by_lot(db, workspace_id=workspace_id)
    lots = {
        lot.id: lot
        for lot in db.execute(select(Lot).where(Lot.workspace_id == workspace_id)).scalars()
    }
    by_part: dict[UUID, dict[str | None, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for lot_id, qty in lot_qty.items():
        if qty <= 0:
            continue
        lot = lots.get(lot_id)
        if lot is None:
            continue
        unit_cost = Decimal(lot.purchase_unit_cost or 0)
        by_part[lot.part_id][lot.purchase_currency] += unit_cost * Decimal(qty)
    return {part_id: dict(values) for part_id, values in by_part.items()}


def _search_replenishment_offers(
    db: Session,
    *,
    workspace: Any,
    mpns: list[str],
    use_cached_data: bool | None,
) -> tuple[dict[str, SourcingSearchResult], ReplenishmentCostStatusOut]:
    results: dict[str, SourcingSearchResult] = {}
    fetched_at_values: list[datetime] = []
    cache_hits: list[bool] = []
    partial = False

    for chunk in sourcing_service.chunk_mpns(sourcing_service.dedupe_mpns(mpns)):
        try:
            out = sourcing_service.search(
                db,
                workspace=workspace,
                mpns=chunk,
                use_cached_data=use_cached_data,
                ttl_seconds=REPLENISHMENT_COST_TTL_SECONDS,
            )
        except SourcingNotConfigured:
            return {}, ReplenishmentCostStatusOut(
                state="not_configured",
                message="sourcing not configured",
                links=sourcing_service.TRUSTEDPARTS_LINKS,
            )
        except SourcingBudgetBlocked:
            partial = True
            break
        except (
            SourcingAuthError,
            SourcingRateLimitError,
            SourcingTimeoutError,
            SourcingClientError,
        ):
            return results, ReplenishmentCostStatusOut(
                state="error",
                message="TrustedParts sourcing unavailable",
                partial=bool(results),
                links=sourcing_service.TRUSTEDPARTS_LINKS,
            )

        fetched_at_values.append(out.fetched_at)
        cache_hits.append(out.cache_hit)
        for result in out.results:
            results[result.mpn.casefold()] = result

    if partial:
        return results, ReplenishmentCostStatusOut(
            state="degraded",
            message="TrustedParts budget exhausted; partial report returned",
            fetched_at=max(fetched_at_values, default=None),
            cache_hit=all(cache_hits) if cache_hits else None,
            partial=True,
            links=sourcing_service.TRUSTEDPARTS_LINKS,
        )

    return results, ReplenishmentCostStatusOut(
        state="ok",
        fetched_at=max(fetched_at_values, default=utcnow()),
        cache_hit=all(cache_hits) if cache_hits else None,
        links=sourcing_service.TRUSTEDPARTS_LINKS,
    )


def _status_ok() -> ReplenishmentCostStatusOut:
    return ReplenishmentCostStatusOut(
        state="ok",
        fetched_at=utcnow(),
        cache_hit=None,
        links=sourcing_service.TRUSTEDPARTS_LINKS,
    )


def _row_for_part(
    part: Part,
    *,
    on_hand: int,
    historical_costs: dict[str | None, Decimal],
    search_result: SourcingSearchResult | None,
    sourcing_status: ReplenishmentCostStatusOut,
) -> ReplenishmentCostRow:
    best_offer = _best_replacement_offer(search_result, qty=on_hand)
    historical_items = [
        CurrencyAmountOut(currency=currency, value=value)
        for currency, value in sorted(historical_costs.items(), key=lambda item: item[0] or "")
    ]
    if not historical_items:
        historical_items = [CurrencyAmountOut(currency=None, value=_MONEY_ZERO)]

    if best_offer is None:
        reason = (
            "sourcing_not_configured"
            if sourcing_status.state == "not_configured"
            else "sourcing_unavailable"
            if sourcing_status.state in {"degraded", "error"}
            else "no_offer"
        )
        return ReplenishmentCostRow(
            part_id=part.id,
            name=part.name,
            manufacturer=part.manufacturer,
            mpn=part.mpn or "",
            on_hand=on_hand,
            currency=_single_currency(historical_costs),
            historical_costs=historical_items,
            historical_cost=_single_historical_cost(historical_costs),
            reason=reason,
        )

    unit_price, currency = best_offer
    replacement_cost = unit_price * Decimal(on_hand)
    historical_cost = historical_costs.get(currency)
    if historical_cost is None:
        return ReplenishmentCostRow(
            part_id=part.id,
            name=part.name,
            manufacturer=part.manufacturer,
            mpn=part.mpn or "",
            on_hand=on_hand,
            currency=currency,
            historical_costs=historical_items,
            replacement_unit_price=unit_price,
            replacement_cost=replacement_cost,
            replacement_currency=currency,
            reason="currency_mismatch",
            source="trustedparts",
        )

    delta_abs = replacement_cost - historical_cost
    delta_pct = None
    if historical_cost != 0:
        delta_pct = ((delta_abs / historical_cost) * Decimal("100")).quantize(
            _PCT_QUANT,
            rounding=ROUND_HALF_UP,
        )
    return ReplenishmentCostRow(
        part_id=part.id,
        name=part.name,
        manufacturer=part.manufacturer,
        mpn=part.mpn or "",
        on_hand=on_hand,
        currency=currency,
        historical_costs=historical_items,
        historical_cost=historical_cost,
        replacement_unit_price=unit_price,
        replacement_cost=replacement_cost,
        replacement_currency=currency,
        delta_abs=delta_abs,
        delta_pct=delta_pct,
        source="trustedparts",
    )


def _best_replacement_offer(
    search_result: SourcingSearchResult | None,
    *,
    qty: int,
) -> tuple[Decimal, str | None] | None:
    if search_result is None or qty < 1:
        return None

    best: tuple[Decimal, str | None] | None = None
    for offer in search_result.offers:
        for distributor in offer.distributors:
            price = sourcing_service._unit_price_for_distributor(distributor, qty)
            if price is None:
                continue
            candidate = (price, distributor.currency)
            if best is None or candidate[0] < best[0]:
                best = candidate
    return best


def _single_currency(values: dict[str | None, Decimal]) -> str | None:
    currencies = list(values.keys())
    if len(currencies) == 1:
        return currencies[0]
    if len(currencies) > 1:
        return "MIXED"
    return None


def _single_historical_cost(values: dict[str | None, Decimal]) -> Decimal | None:
    if len(values) == 1:
        return next(iter(values.values()))
    return None


def _totals(rows: list[ReplenishmentCostRow]) -> list[ReplenishmentCostTotalOut]:
    historical: dict[str | None, Decimal] = defaultdict(Decimal)
    replacement: dict[str | None, Decimal] = defaultdict(Decimal)
    for row in rows:
        for item in row.historical_costs:
            historical[item.currency] += item.value
        if row.replacement_cost is not None:
            replacement[row.replacement_currency] += row.replacement_cost

    currencies = sorted(set(historical) | set(replacement), key=lambda item: item or "")
    return [
        ReplenishmentCostTotalOut(
            currency=currency,
            historical_cost=historical.get(currency, _MONEY_ZERO),
            replacement_cost=replacement.get(currency, _MONEY_ZERO),
            delta_abs=(
                replacement.get(currency, _MONEY_ZERO) - historical.get(currency, _MONEY_ZERO)
                if currency in replacement
                else None
            ),
        )
        for currency in currencies
    ]


def _sort_key(sort: ReplenishmentCostSort):
    if sort == "name":
        return lambda row: (row.name.casefold(), row.mpn.casefold())
    if sort == "delta_abs":
        return lambda row: (
            row.delta_abs is None,
            -(row.delta_abs or _MONEY_ZERO),
            row.name.casefold(),
        )
    return lambda row: (
        row.delta_pct is None,
        -(row.delta_pct or _MONEY_ZERO),
        row.name.casefold(),
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


def low_stock_report(
    db: Session,
    *,
    workspace: Any,
    include_sourcing: bool = False,
    requested_by: UUID | None = None,
) -> list[dict[str, Any]] | dict[str, Any]:
    parts = _low_stock_candidate_parts(db, workspace_id=workspace.id)
    rows = _low_stock_rows(db, workspace_id=workspace.id, parts=parts)
    if not include_sourcing:
        return rows

    for row in rows:
        row["sourcing"] = None

    status = _attach_low_stock_sourcing(
        db,
        workspace=workspace,
        rows=rows,
        requested_by=requested_by,
    )
    return {
        "rows": rows,
        "sourcing_status": status,
        "powered_by": "TrustedParts" if status in {"ok", "partial", "budget_blocked"} else None,
        "links": (
            sourcing_service.TRUSTEDPARTS_LINKS
            if status in {"ok", "partial", "budget_blocked"}
            else None
        ),
    }


def _low_stock_candidate_parts(db: Session, *, workspace_id: UUID) -> list[Part]:
    return list(
        db.execute(
            select(Part)
            .where(Part.workspace_id == workspace_id)
            .where(Part.archived_at.is_(None))
            .where(Part.low_stock_report_quantity.is_not(None))
        ).scalars()
    )


def _low_stock_rows(
    db: Session,
    *,
    workspace_id: UUID,
    parts: list[Part],
) -> list[dict[str, Any]]:
    part_ids = [part.id for part in parts]
    on_hand = bulk_current_quantities(
        db, workspace_id=workspace_id, part_ids=part_ids, status="on_hand"
    )
    reserved = bulk_current_quantities(
        db, workspace_id=workspace_id, part_ids=part_ids, status="reserved"
    )

    rows: list[dict[str, Any]] = []
    for part in parts:
        cur = on_hand.get(part.id, 0)
        res = reserved.get(part.id, 0)
        avail = cur - res
        threshold = part.low_stock_report_quantity or 0
        if avail < threshold:
            rows.append(
                {
                    "part_id": str(part.id),
                    "name": part.name,
                    "manufacturer": part.manufacturer,
                    "mpn": part.mpn,
                    "on_hand": cur,
                    "reserved": res,
                    "available": avail,
                    "threshold": threshold,
                    "short_by": threshold - avail,
                }
            )
    rows.sort(key=lambda row: row["short_by"], reverse=True)
    return rows


def _attach_low_stock_sourcing(
    db: Session,
    *,
    workspace: Any,
    rows: list[dict[str, Any]],
    requested_by: UUID | None,
) -> SourcingStatus:
    mpns = sourcing_service.dedupe_mpns(row.get("mpn") for row in rows)
    if not mpns:
        return "ok"

    search_results: dict[str, Any] = {}
    partial = False
    try:
        for chunk in sourcing_service.chunk_mpns(mpns):
            verdict = sourcing_service.BUDGET.check(workspace.id, parts_count=len(chunk))
            if not verdict.allow:
                return "budget_blocked" if not search_results else "partial"
            partial = partial or verdict.mode == "degraded"
            out = sourcing_service.search(
                db,
                workspace=workspace,
                mpns=chunk,
                use_cached_data=bool(workspace.sourcing_use_cached_for_dashboards),
                ttl_seconds=LOW_STOCK_SOURCING_TTL_SECONDS,
                requested_by=requested_by,
            )
            for result in out.results:
                search_results[result.mpn.casefold()] = result
    except sourcing_service.SourcingNotConfigured:
        return "not_configured"
    except sourcing_service.SourcingBudgetBlocked:
        return "budget_blocked" if not search_results else "partial"
    except Exception:
        _log.exception(
            "low-stock sourcing enrichment failed",
            extra={"workspace_id": str(workspace.id)},
        )
        return "partial"

    preferred = _preferred_distributors(workspace.sourcing_preferred_distributors)
    preferred_keys = {item.casefold() for item in preferred}
    for row in rows:
        mpn = row.get("mpn")
        if not mpn:
            continue
        result = search_results.get(str(mpn).casefold())
        if result is None:
            continue
        short_by = max(1, int(row["short_by"]))
        offers = sourcing_service._joined_offers([str(mpn)], search_results, qty=short_by)
        best_offer = sourcing_service._best_offer_at_qty(offers, short_by)
        row["sourcing"] = SourcingReportData(
            authorized_stock=sum(offer.stock for offer in offers),
            offers=offers,
            best_offer=best_offer,
            est_replenishment_cost=_extended_cost(best_offer, short_by),
            lead_time_days=best_offer.lead_time_days if best_offer is not None else None,
            preferred_distributor_available=(
                bool(preferred_keys)
                and any(
                    offer.distributor.casefold() in preferred_keys and offer.stock > 0
                    for offer in offers
                )
            ),
            cache_hit=result.cache_hit,
            fetched_at=result.fetched_at,
        )
    return "partial" if partial else "ok"


def _extended_cost(best_offer: Any, quantity: int) -> Decimal | None:
    if best_offer is None or best_offer.unit_price is None:
        return None
    purchase_quantity = max(quantity, int(best_offer.moq or 1))
    return best_offer.unit_price * Decimal(purchase_quantity)


def _clean_distributors(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _preferred_distributors(value: Any) -> list[str]:
    return _clean_distributors(value)
