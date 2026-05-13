"""Deterministic purchase-plan optimizer for sourced BOM rows."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID

from app.domain.sourcing.constants import INFINITE_LEAD_TIME_DAYS
from app.domain.sourcing.pricing import best_unit_price_at_qty
from app.domain.sourcing.schemas import SourcingBomLineOut, SourcingBomOfferOut

Strategy = Literal[
    "lowest_total_price",
    "fewest_distributors",
    "fastest_availability",
    "preferred_first",
]

_VALID_STRATEGIES: set[str] = {
    "lowest_total_price",
    "fewest_distributors",
    "fastest_availability",
    "preferred_first",
}
_INFINITE_PRICE = Decimal("Infinity")


@dataclass(frozen=True)
class OptimizerSelection:
    """One selected offer for one shortage line."""

    project_entry_id: UUID
    part_id: UUID
    mpn_searched: str
    required_qty: int
    internal_available_qty: int
    shortage_qty: int
    selected_distributor: str | None
    selected_qty: int
    selected_unit_price: Decimal | None
    selected_currency: str | None
    selected_packaging: str | None
    selected_moq: int | None
    selected_lead_time_days: int | None
    selected_url: str | None
    risk_flags: tuple[str, ...]


@dataclass(frozen=True)
class OptimizerOutcome:
    selections: list[OptimizerSelection]
    unfilled_lines: list[UUID]
    distributors_used: list[str]
    est_total_cost: Decimal | None
    worst_lead_time_days: int | None


@dataclass(frozen=True)
class _Candidate:
    line: SourcingBomLineOut
    offer: SourcingBomOfferOut
    selected_qty: int
    unit_price: Decimal
    extended_cost: Decimal


def optimize(
    bom_rows: list[SourcingBomLineOut],
    *,
    strategy: Strategy = "preferred_first",
    preferred_distributors: list[str] | None = None,
    max_distributors: int | None = None,
    moq_overbuy_cap: int | None = None,
    price_tolerance_pct: Decimal = Decimal("5"),
) -> OptimizerOutcome:
    """Select one offer per BOM shortage line.

    Tie-break order is strategy-specific first, then deterministic fallback:
    preferred distributor order where applicable, distributor alphabetically,
    MPN alphabetically, and URL alphabetically.
    """

    if strategy not in _VALID_STRATEGIES:
        raise ValueError(f"unsupported optimizer strategy: {strategy}")
    if max_distributors is not None and max_distributors < 1:
        raise ValueError("max_distributors must be positive")
    if moq_overbuy_cap is not None and moq_overbuy_cap < 1:
        raise ValueError("moq_overbuy_cap must be positive")

    rows = sorted(bom_rows, key=lambda row: (str(row.project_entry_id), row.mpn or ""))
    if strategy == "fewest_distributors":
        selections = _optimize_fewest_distributors(
            rows,
            max_distributors=max_distributors,
            moq_overbuy_cap=moq_overbuy_cap,
        )
    else:
        preferred_rank = _preferred_rank(preferred_distributors)
        selectors = {
            "lowest_total_price": lambda row: _select_lowest_total_price(
                row,
                moq_overbuy_cap=moq_overbuy_cap,
            ),
            "fastest_availability": lambda row: _select_fastest_availability(
                row,
                moq_overbuy_cap=moq_overbuy_cap,
            ),
            "preferred_first": lambda row: _select_preferred_first(
                row,
                preferred_rank=preferred_rank,
                moq_overbuy_cap=moq_overbuy_cap,
                price_tolerance_pct=price_tolerance_pct,
            ),
        }
        selections = [selectors[strategy](row) for row in rows]

    return _outcome(selections)


def _optimize_fewest_distributors(
    rows: list[SourcingBomLineOut],
    *,
    max_distributors: int | None,
    moq_overbuy_cap: int | None,
) -> list[OptimizerSelection]:
    remaining = {row.project_entry_id: row for row in rows}
    selected_by_line: dict[UUID, OptimizerSelection] = {}
    distributors_used = 0

    while remaining and (
        max_distributors is None or distributors_used < max_distributors
    ):
        candidates_by_distributor: dict[str, list[_Candidate]] = {}
        display_names: dict[str, str] = {}
        for row in remaining.values():
            for candidate in _candidates(row, moq_overbuy_cap=moq_overbuy_cap):
                key = candidate.offer.distributor.casefold()
                candidates_by_distributor.setdefault(key, []).append(candidate)
                display_names.setdefault(key, candidate.offer.distributor)

        if not candidates_by_distributor:
            break

        best_key = min(
            candidates_by_distributor,
            key=lambda key: _fewest_distributor_key(
                key,
                candidates_by_distributor=candidates_by_distributor,
                display_names=display_names,
            ),
        )
        best_for_lines = _best_candidates_for_distributor(
            candidates_by_distributor[best_key]
        )
        for line_id, candidate in sorted(
            best_for_lines.items(),
            key=lambda item: str(item[0]),
        ):
            selected_by_line[line_id] = _selection_from_candidate(candidate)
            remaining.pop(line_id, None)
        distributors_used += 1

    for row in remaining.values():
        selected_by_line[row.project_entry_id] = _unfilled_selection(row)

    return [
        selected_by_line[row.project_entry_id]
        for row in rows
        if row.project_entry_id in selected_by_line
    ]


def _select_lowest_total_price(
    row: SourcingBomLineOut,
    *,
    moq_overbuy_cap: int | None,
) -> OptimizerSelection:
    candidates = _candidates(row, moq_overbuy_cap=moq_overbuy_cap)
    if not candidates:
        return _unfilled_selection(row)
    return _selection_from_candidate(
        min(candidates, key=lambda candidate: _lowest_price_key(candidate))
    )


def _select_fastest_availability(
    row: SourcingBomLineOut,
    *,
    moq_overbuy_cap: int | None,
) -> OptimizerSelection:
    candidates = _candidates(row, moq_overbuy_cap=moq_overbuy_cap)
    if not candidates:
        return _unfilled_selection(row)
    return _selection_from_candidate(
        min(candidates, key=lambda candidate: _fastest_key(candidate))
    )


def _select_preferred_first(
    row: SourcingBomLineOut,
    *,
    preferred_rank: dict[str, int],
    moq_overbuy_cap: int | None,
    price_tolerance_pct: Decimal,
) -> OptimizerSelection:
    candidates = _candidates(row, moq_overbuy_cap=moq_overbuy_cap)
    if not candidates:
        return _unfilled_selection(row)

    cheapest = min(candidates, key=lambda candidate: _lowest_unit_price_key(candidate))
    tolerance = price_tolerance_pct / Decimal("100")
    preferred_candidates = [
        candidate
        for candidate in candidates
        if candidate.offer.distributor.casefold() in preferred_rank
        and _within_tolerance(
            candidate.unit_price,
            cheapest=cheapest.unit_price,
            tolerance=tolerance,
        )
    ]
    if preferred_candidates:
        return _selection_from_candidate(
            min(
                preferred_candidates,
                key=lambda candidate: (
                    preferred_rank[candidate.offer.distributor.casefold()],
                    candidate.unit_price,
                    _candidate_alpha_key(candidate),
                ),
            )
        )
    return _selection_from_candidate(cheapest)


def _candidates(
    row: SourcingBomLineOut,
    *,
    moq_overbuy_cap: int | None,
) -> list[_Candidate]:
    if row.short_by < 1:
        return []

    out: list[_Candidate] = []
    for offer in row.offers:
        selected_qty = _selected_qty(row.short_by, offer.moq)
        if moq_overbuy_cap is not None and selected_qty > row.short_by * moq_overbuy_cap:
            continue
        if offer.stock < selected_qty:
            continue
        unit_price = _unit_price(offer, selected_qty)
        if unit_price is None:
            continue
        out.append(
            _Candidate(
                line=row,
                offer=offer,
                selected_qty=selected_qty,
                unit_price=unit_price,
                extended_cost=unit_price * Decimal(selected_qty),
            )
        )
    out.sort(key=_candidate_alpha_key)
    return out


def _selected_qty(shortage_qty: int, moq: int | None) -> int:
    return max(shortage_qty, int(moq or 1))


def _unit_price(offer: SourcingBomOfferOut, qty: int) -> Decimal | None:
    best = best_unit_price_at_qty(offer.price_breaks, qty)
    if best is not None:
        return best[0]
    return offer.unit_price


def _lowest_price_key(candidate: _Candidate) -> tuple[Decimal, tuple[str, str, str]]:
    return candidate.extended_cost, _candidate_alpha_key(candidate)


def _lowest_unit_price_key(
    candidate: _Candidate,
) -> tuple[Decimal, Decimal, tuple[str, str, str]]:
    return candidate.unit_price, candidate.extended_cost, _candidate_alpha_key(candidate)


def _fastest_key(candidate: _Candidate) -> tuple[int, Decimal, tuple[str, str, str]]:
    lead_time = (
        candidate.offer.lead_time_days
        if candidate.offer.lead_time_days is not None
        else INFINITE_LEAD_TIME_DAYS
    )
    return lead_time, candidate.unit_price, _candidate_alpha_key(candidate)


def _fewest_distributor_key(
    distributor_key: str,
    *,
    candidates_by_distributor: dict[str, list[_Candidate]],
    display_names: dict[str, str],
) -> tuple[int, Decimal, str]:
    best_by_line = _best_candidates_for_distributor(
        candidates_by_distributor[distributor_key]
    )
    total_cost = sum(
        (candidate.extended_cost for candidate in best_by_line.values()),
        Decimal("0"),
    )
    return -len(best_by_line), total_cost, display_names[distributor_key].casefold()


def _best_candidates_for_distributor(
    candidates: list[_Candidate],
) -> dict[UUID, _Candidate]:
    best_by_line: dict[UUID, _Candidate] = {}
    for candidate in candidates:
        line_id = candidate.line.project_entry_id
        current = best_by_line.get(line_id)
        if current is None or _lowest_price_key(candidate) < _lowest_price_key(current):
            best_by_line[line_id] = candidate
    return best_by_line


def _candidate_alpha_key(candidate: _Candidate) -> tuple[str, str, str]:
    return (
        candidate.offer.distributor.casefold(),
        candidate.offer.mpn.casefold(),
        candidate.offer.url or "",
    )


def _selection_from_candidate(candidate: _Candidate) -> OptimizerSelection:
    row = candidate.line
    offer = candidate.offer
    return OptimizerSelection(
        project_entry_id=row.project_entry_id,
        part_id=row.part_id,
        mpn_searched=row.mpn or offer.mpn,
        required_qty=row.required,
        internal_available_qty=row.available + row.substitute_available,
        shortage_qty=row.short_by,
        selected_distributor=offer.distributor,
        selected_qty=candidate.selected_qty,
        selected_unit_price=candidate.unit_price,
        selected_currency=offer.currency,
        selected_packaging=offer.packaging,
        selected_moq=offer.moq,
        selected_lead_time_days=offer.lead_time_days,
        selected_url=offer.url,
        risk_flags=tuple(row.risk_flags),
    )


def _unfilled_selection(row: SourcingBomLineOut) -> OptimizerSelection:
    return OptimizerSelection(
        project_entry_id=row.project_entry_id,
        part_id=row.part_id,
        mpn_searched=row.mpn or "",
        required_qty=row.required,
        internal_available_qty=row.available + row.substitute_available,
        shortage_qty=row.short_by,
        selected_distributor=None,
        selected_qty=0,
        selected_unit_price=None,
        selected_currency=None,
        selected_packaging=None,
        selected_moq=None,
        selected_lead_time_days=None,
        selected_url=None,
        risk_flags=tuple(row.risk_flags),
    )


def _outcome(selections: list[OptimizerSelection]) -> OptimizerOutcome:
    unfilled = [
        selection.project_entry_id
        for selection in selections
        if selection.selected_distributor is None
    ]
    distributors = sorted(
        {
            selection.selected_distributor
            for selection in selections
            if selection.selected_distributor is not None
        },
        key=str.casefold,
    )
    if unfilled:
        est_total_cost = None
    else:
        est_total_cost = sum(
            (
                selection.selected_unit_price * Decimal(selection.selected_qty)
                for selection in selections
                if selection.selected_unit_price is not None
            ),
            Decimal("0"),
        )
    lead_times = [
        selection.selected_lead_time_days
        for selection in selections
        if selection.selected_lead_time_days is not None
    ]
    return OptimizerOutcome(
        selections=selections,
        unfilled_lines=unfilled,
        distributors_used=distributors,
        est_total_cost=est_total_cost,
        worst_lead_time_days=max(lead_times) if lead_times else None,
    )


def _preferred_rank(preferred_distributors: list[str] | None) -> dict[str, int]:
    if not preferred_distributors:
        return {}
    return {
        distributor.strip().casefold(): index
        for index, distributor in enumerate(preferred_distributors)
        if distributor.strip()
    }


def _within_tolerance(
    unit_price: Decimal,
    *,
    cheapest: Decimal,
    tolerance: Decimal,
) -> bool:
    if cheapest <= Decimal("0"):
        return unit_price == cheapest
    return (unit_price - cheapest) / cheapest <= tolerance
