"""Distributor coverage matrix computation for sourced BOM rows."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations
from uuid import UUID

from app.domain.sourcing.pricing import best_unit_price_at_qty
from app.domain.sourcing.schemas import SourcingBomLineOut, SourcingBomOfferOut

EXHAUSTIVE_COMBO_DISTRIBUTOR_LIMIT = 30


@dataclass(frozen=True)
class DistributorCoverageRow:
    distributor: str
    lines_covered: int
    lines_uncovered: list[UUID]
    coverage_pct: float
    est_total_cost: Decimal | None
    worst_lead_time_days: int | None


@dataclass(frozen=True)
class DistributorCoverageMatrix:
    rows: list[DistributorCoverageRow]
    total_lines: int
    best_single_distributor: str | None
    best_two_distributor_combo: tuple[str, str] | None


@dataclass(frozen=True)
class BuildCapacity:
    can_build_now: int
    can_build_after_purchase: int
    est_purchase_cost: Decimal | None
    blocking_lines_now: list[UUID]
    blocking_lines_after_purchase: list[UUID]


def compute_build_capacity(
    bom_rows: list[SourcingBomLineOut],
    *,
    requested_build_quantity: int,
) -> BuildCapacity:
    """Compute build capacity from enriched BOM sourcing rows."""
    effective_rows = [row for row in bom_rows if row.required > 0]
    if not effective_rows:
        return BuildCapacity(
            can_build_now=0,
            can_build_after_purchase=0,
            est_purchase_cost=None,
            blocking_lines_now=[],
            blocking_lines_after_purchase=[],
        )

    ratios_now = {
        row.project_entry_id: _supported_builds(
            row.available + row.substitute_available,
            required=row.required,
            requested_build_quantity=requested_build_quantity,
        )
        for row in effective_rows
    }
    ratios_after_purchase = {
        row.project_entry_id: _supported_builds(
            row.available + row.substitute_available + row.authorized_stock,
            required=row.required,
            requested_build_quantity=requested_build_quantity,
        )
        for row in effective_rows
    }

    can_build_now = min(ratios_now.values())
    can_build_after_purchase = min(ratios_after_purchase.values())
    blocking_lines_now = [
        row.project_entry_id
        for row in effective_rows
        if ratios_now[row.project_entry_id] == can_build_now
    ]
    blocking_lines_after_purchase = [
        row.project_entry_id
        for row in effective_rows
        if ratios_after_purchase[row.project_entry_id] == can_build_after_purchase
    ]

    est_purchase_cost = Decimal("0")
    blocking_after = set(blocking_lines_after_purchase)
    for row in effective_rows:
        purchase_qty = max(
            0,
            _required_for_builds(
                row.required,
                builds=can_build_after_purchase,
                requested_build_quantity=requested_build_quantity,
            )
            - (row.available + row.substitute_available),
        )
        if purchase_qty == 0:
            if (
                row.project_entry_id in blocking_after
                and ratios_after_purchase[row.project_entry_id] < requested_build_quantity
                and row.best_offer is None
            ):
                est_purchase_cost = None
            continue
        if row.best_offer is None or row.best_offer.unit_price is None:
            est_purchase_cost = None
            continue
        if est_purchase_cost is not None:
            est_purchase_cost += row.best_offer.unit_price * Decimal(purchase_qty)

    return BuildCapacity(
        can_build_now=can_build_now,
        can_build_after_purchase=can_build_after_purchase,
        est_purchase_cost=est_purchase_cost,
        blocking_lines_now=blocking_lines_now,
        blocking_lines_after_purchase=blocking_lines_after_purchase,
    )


def compute_coverage(
    bom_rows: list[SourcingBomLineOut],
    *,
    preferred_distributors: list[str] | None = None,
) -> DistributorCoverageMatrix:
    """Compute per-distributor BOM line coverage.

    Distributor-pair selection is exhaustive up to 30 distinct distributors.
    Above that threshold it uses the documented greedy fallback: pick the
    distributor covering the most lines, then the distributor covering the most
    remaining uncovered lines.
    """
    total_lines = len(bom_rows)
    if total_lines == 0:
        return DistributorCoverageMatrix(
            rows=[],
            total_lines=0,
            best_single_distributor=None,
            best_two_distributor_combo=None,
        )

    offers_by_distributor: dict[str, list[SourcingBomOfferOut]] = {}
    display_names: dict[str, str] = {}
    for line in bom_rows:
        for offer in line.offers:
            key = offer.distributor.casefold()
            offers_by_distributor.setdefault(key, []).append(offer)
            display_names.setdefault(key, offer.distributor)

    preferred_rank = _preferred_rank(preferred_distributors)
    line_ids = [line.project_entry_id for line in bom_rows]
    covered_by_distributor: dict[str, set[UUID]] = {}
    row_costs: dict[str, Decimal | None] = {}
    row_lead_times: dict[str, int | None] = {}

    for key in offers_by_distributor:
        covered: set[UUID] = set()
        total_cost: Decimal | None = Decimal("0")
        worst_lead_time: int | None = None
        for line in bom_rows:
            best_offer = _best_covering_offer(key, line)
            if best_offer is None:
                continue
            covered.add(line.project_entry_id)
            line_cost = _offer_extended_cost(best_offer, line.short_by)
            if line_cost is None:
                total_cost = None
            elif total_cost is not None:
                total_cost += line_cost
            if best_offer.lead_time_days is not None:
                worst_lead_time = (
                    best_offer.lead_time_days
                    if worst_lead_time is None
                    else max(worst_lead_time, best_offer.lead_time_days)
                )
        covered_by_distributor[key] = covered
        row_costs[key] = total_cost
        row_lead_times[key] = worst_lead_time

    rows = [
        DistributorCoverageRow(
            distributor=display_names[key],
            lines_covered=len(covered),
            lines_uncovered=[line_id for line_id in line_ids if line_id not in covered],
            coverage_pct=len(covered) / total_lines,
            est_total_cost=row_costs[key],
            worst_lead_time_days=row_lead_times[key],
        )
        for key, covered in covered_by_distributor.items()
    ]
    rows.sort(key=lambda row: _single_sort_key(row, preferred_rank))

    best_single = rows[0].distributor if rows else None
    best_two = _best_two_distributors(
        rows,
        covered_by_distributor=covered_by_distributor,
        preferred_rank=preferred_rank,
        total_lines=total_lines,
    )

    return DistributorCoverageMatrix(
        rows=rows,
        total_lines=total_lines,
        best_single_distributor=best_single,
        best_two_distributor_combo=best_two,
    )


def _supported_builds(
    available: int,
    *,
    required: int,
    requested_build_quantity: int,
) -> int:
    if available <= 0 or requested_build_quantity <= 0:
        return 0
    return available * requested_build_quantity // required


def _required_for_builds(
    required: int,
    *,
    builds: int,
    requested_build_quantity: int,
) -> int:
    if builds <= 0 or requested_build_quantity <= 0:
        return 0
    return (required * builds + requested_build_quantity - 1) // requested_build_quantity


def _best_covering_offer(
    distributor_key: str,
    line: SourcingBomLineOut,
) -> SourcingBomOfferOut | None:
    candidates = [
        offer
        for offer in line.offers
        if offer.distributor.casefold() == distributor_key and offer.stock >= line.short_by
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda offer: (
            _price_sort_value(_offer_unit_price(offer, line.short_by)),
            offer.lead_time_days if offer.lead_time_days is not None else 10**9,
            offer.mpn.casefold(),
        ),
    )


def _offer_extended_cost(offer: SourcingBomOfferOut, qty: int) -> Decimal | None:
    unit_price = _offer_unit_price(offer, qty)
    if unit_price is None:
        return None
    return unit_price * Decimal(qty)


def _offer_unit_price(offer: SourcingBomOfferOut, qty: int) -> Decimal | None:
    best = best_unit_price_at_qty(offer.price_breaks, qty)
    if best is not None:
        return best[0]
    return offer.unit_price


def _best_two_distributors(
    rows: list[DistributorCoverageRow],
    *,
    covered_by_distributor: dict[str, set[UUID]],
    preferred_rank: dict[str, int],
    total_lines: int,
) -> tuple[str, str] | None:
    if len(rows) < 2 or rows[0].lines_covered == total_lines:
        return None

    rows_by_key = {row.distributor.casefold(): row for row in rows}
    keys = [row.distributor.casefold() for row in rows]
    if len(keys) <= EXHAUSTIVE_COMBO_DISTRIBUTOR_LIMIT:
        first_key, second_key = min(
            combinations(keys, 2),
            key=lambda pair: _pair_sort_key(
                pair,
                rows_by_key=rows_by_key,
                covered_by_distributor=covered_by_distributor,
                preferred_rank=preferred_rank,
            ),
        )
    else:
        first_key = rows[0].distributor.casefold()
        remaining = (
            set().union(*covered_by_distributor.values())
            - covered_by_distributor[first_key]
        )
        second_key = min(
            (key for key in keys if key != first_key),
            key=lambda key: (
                -len(covered_by_distributor[key] & remaining),
                _single_sort_key(rows_by_key[key], preferred_rank),
            ),
        )

    return rows_by_key[first_key].distributor, rows_by_key[second_key].distributor


def _pair_sort_key(
    pair: tuple[str, str],
    *,
    rows_by_key: dict[str, DistributorCoverageRow],
    covered_by_distributor: dict[str, set[UUID]],
    preferred_rank: dict[str, int],
) -> tuple[int, tuple[object, ...], tuple[object, ...]]:
    first, second = sorted(
        pair,
        key=lambda key: _single_sort_key(rows_by_key[key], preferred_rank),
    )
    covered = covered_by_distributor[first] | covered_by_distributor[second]
    return (
        -len(covered),
        _single_sort_key(rows_by_key[first], preferred_rank),
        _single_sort_key(rows_by_key[second], preferred_rank),
    )


def _single_sort_key(
    row: DistributorCoverageRow,
    preferred_rank: dict[str, int],
) -> tuple[object, ...]:
    return (
        -row.lines_covered,
        preferred_rank.get(row.distributor.casefold(), 10**9),
        _price_sort_value(row.est_total_cost),
        row.distributor.casefold(),
    )


def _preferred_rank(preferred_distributors: list[str] | None) -> dict[str, int]:
    if not preferred_distributors:
        return {}
    return {
        distributor.strip().casefold(): index
        for index, distributor in enumerate(preferred_distributors)
        if distributor.strip()
    }


def _price_sort_value(value: Decimal | None) -> Decimal:
    return value if value is not None else Decimal("Infinity")
