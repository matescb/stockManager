"""Service facade for TrustedParts sourcing."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api._helpers import assert_in_workspace
from app.core.errors import ErrorCodes, raise_http
from app.core.time import utcnow
from app.domain.builds.service import shortage_analysis
from app.domain.orders.models import Order, OrderEntry
from app.domain.parts.models import Part
from app.domain.projects.models import Project
from app.domain.sourcing import cache
from app.domain.sourcing.budget import BUDGET
from app.domain.sourcing.coverage import compute_build_capacity, compute_coverage
from app.domain.sourcing.factory import make_sourcing_provider
from app.domain.sourcing.models import PurchasePlan, PurchasePlanLine, SourcingAlert
from app.domain.sourcing.optimizer import Strategy, optimize
from app.domain.sourcing.pricing import best_unit_price_at_qty
from app.domain.sourcing.schemas import (
    BackInStockThreshold,
    BomBuyableThreshold,
    BuildCapacityOut,
    DistributorCoverageMatrixOut,
    OutOfAuthorizedStockThreshold,
    PriceChangedThreshold,
    PurchasePlanOrderOverrideIn,
    PurchasePlanOrdersOut,
    PurchasePlanOut,
    SourcingAlertIn,
    SourcingAlertPatch,
    SourcingAlertType,
    SourcingAttributionLinks,
    SourcingBomLineOut,
    SourcingBomOfferOut,
    SourcingBomOut,
    SourcingBomPriceBreakOut,
    SourcingQuery,
    SourcingSearchOut,
    SourcingSearchRaw,
    SourcingSearchResult,
    StockAboveThreshold,
    StockBelowThreshold,
    StringChangedThreshold,
    TariffStatusChangedThreshold,
)
from app.domain.workspaces.models import WorkspaceMember

TTL_SECONDS = 30 * 60
BOM_TTL_SECONDS = 10 * 60
PURCHASE_PLAN_TTL = timedelta(days=7)
MAX_PLAN_STALENESS_SECONDS = 600
TRUSTEDPARTS_LINKS = SourcingAttributionLinks(
    primary="https://www.trustedparts.com/",
    attribution="https://www.trustedparts.com/en/about",
)
TARGET_ROHS_REGION = "EU"
_SOURCING_FILTER_ALERT_TYPES = {
    "back_in_stock",
    "out_of_authorized_stock",
    "price_changed",
    "lifecycle_risk_changed",
    "supply_chain_risk_changed",
    "tariff_status_changed",
}
_THRESHOLD_SCHEMAS: dict[SourcingAlertType, type[BaseModel]] = {
    "stock_below": StockBelowThreshold,
    "stock_above": StockAboveThreshold,
    "back_in_stock": BackInStockThreshold,
    "out_of_authorized_stock": OutOfAuthorizedStockThreshold,
    "price_changed": PriceChangedThreshold,
    "bom_buyable": BomBuyableThreshold,
    "lifecycle_risk_changed": StringChangedThreshold,
    "supply_chain_risk_changed": StringChangedThreshold,
    "tariff_status_changed": TariffStatusChangedThreshold,
}


@dataclass(frozen=True)
class _LineUpdate:
    line: PurchasePlanLine
    selected_distributor: str | None
    selected_qty: int | None
    selected_unit_price: Decimal | None
    selected_currency: str | None
    selected_packaging: str | None
    selected_moq: int | None
    selected_lead_time_days: int | None
    selected_url: str | None


class SourcingNotConfigured(Exception):
    """Workspace has no usable TrustedParts sourcing configuration."""


class SourcingBudgetBlocked(Exception):
    """Workspace exceeded the hard TrustedParts parts-count budget."""


class PurchasePlanStaleError(Exception):
    """Purchase plan must be refreshed before conversion."""


class PurchasePlanCurrencyError(Exception):
    """Purchase plan has incompatible currencies in one distributor group."""


class PurchasePlanOverrideError(Exception):
    """Purchase plan conversion override is not valid for cached line offers."""


def create_alert(
    db: Session,
    *,
    workspace: Any,
    user_id: UUID | None,
    payload: SourcingAlertIn,
) -> SourcingAlert:
    values = _validated_alert_values(
        db,
        workspace=workspace,
        alert_type=payload.alert_type,
        part_id=payload.part_id,
        project_id=payload.project_id,
        threshold=payload.threshold,
        country_code=payload.country_code,
        currency_code=payload.currency_code,
        distributor_filter=payload.distributor_filter,
        notify_user_ids=payload.notify_user_ids,
        cooldown_seconds=payload.cooldown_seconds,
        enabled=payload.enabled,
    )
    alert = SourcingAlert(
        workspace_id=workspace.id,
        created_by=user_id,
        **values,
    )
    db.add(alert)
    db.flush()
    return alert


def list_alerts(
    db: Session,
    *,
    workspace: Any,
    enabled: bool | None = None,
    alert_type: str | None = None,
    part_id: UUID | None = None,
    project_id: UUID | None = None,
    include_archived: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[SourcingAlert]:
    if part_id is not None:
        assert_in_workspace(db, Part, part_id, workspace.id, label="part")
    if project_id is not None:
        assert_in_workspace(db, Project, project_id, workspace.id, label="project")

    stmt = select(SourcingAlert).where(SourcingAlert.workspace_id == workspace.id)
    if not include_archived:
        stmt = stmt.where(SourcingAlert.archived_at.is_(None))
    if enabled is not None:
        stmt = stmt.where(SourcingAlert.enabled.is_(enabled))
    if alert_type is not None:
        stmt = stmt.where(SourcingAlert.alert_type == alert_type)
    if part_id is not None:
        stmt = stmt.where(SourcingAlert.part_id == part_id)
    if project_id is not None:
        stmt = stmt.where(SourcingAlert.project_id == project_id)
    stmt = stmt.order_by(SourcingAlert.created_at.desc(), SourcingAlert.id)
    stmt = stmt.limit(limit).offset(offset)
    return list(db.execute(stmt).scalars())


def get_alert(
    db: Session,
    *,
    workspace: Any,
    alert_id: UUID,
) -> SourcingAlert:
    alert = db.execute(
        select(SourcingAlert).where(
            SourcingAlert.id == alert_id,
            SourcingAlert.workspace_id == workspace.id,
            SourcingAlert.archived_at.is_(None),
        )
    ).scalar_one_or_none()
    if alert is None:
        raise_http(
            404,
            ErrorCodes.RESOURCE_NOT_FOUND,
            "sourcing alert not found",
            resource="sourcing_alert",
        )
    return alert


def update_alert(
    db: Session,
    *,
    workspace: Any,
    alert_id: UUID,
    payload: SourcingAlertPatch,
) -> SourcingAlert:
    if "alert_type" in payload.model_fields_set:
        raise_http(
            422,
            "sourcing_alert.alert_type_immutable",
            "alert_type is immutable",
        )

    alert = get_alert(db, workspace=workspace, alert_id=alert_id)
    data = payload.model_dump(exclude_unset=True)
    values = _validated_alert_values(
        db,
        workspace=workspace,
        alert_type=alert.alert_type,
        part_id=data.get("part_id", alert.part_id),
        project_id=data.get("project_id", alert.project_id),
        threshold=data.get("threshold", alert.threshold),
        country_code=data.get("country_code", alert.country_code),
        currency_code=data.get("currency_code", alert.currency_code),
        distributor_filter=data.get("distributor_filter", alert.distributor_filter),
        notify_user_ids=data.get("notify_user_ids", alert.notify_user_ids),
        cooldown_seconds=data.get("cooldown_seconds", alert.cooldown_seconds),
        enabled=data.get("enabled", alert.enabled),
    )
    for key, value in values.items():
        setattr(alert, key, value)
    db.flush()
    return alert


def archive_alert(
    db: Session,
    *,
    workspace: Any,
    alert_id: UUID,
) -> SourcingAlert:
    alert = get_alert(db, workspace=workspace, alert_id=alert_id)
    if alert.archived_at is None:
        alert.archived_at = utcnow()
        db.flush()
    return alert


def _validated_alert_values(
    db: Session,
    *,
    workspace: Any,
    alert_type: SourcingAlertType | str,
    part_id: UUID | None,
    project_id: UUID | None,
    threshold: dict[str, Any],
    country_code: str | None,
    currency_code: str | None,
    distributor_filter: list[str] | None,
    notify_user_ids: list[UUID] | list[str] | None,
    cooldown_seconds: int,
    enabled: bool,
) -> dict[str, Any]:
    if threshold is None:
        raise_http(422, "sourcing_alert.invalid_threshold", "threshold is required")
    if cooldown_seconds is None:
        raise_http(422, "sourcing_alert.invalid_cooldown", "cooldown_seconds is required")
    if enabled is None:
        raise_http(422, "sourcing_alert.invalid_enabled", "enabled is required")
    if alert_type not in _THRESHOLD_SCHEMAS:
        raise_http(422, "sourcing_alert.invalid_type", "invalid alert_type")
    if (part_id is None) == (project_id is None):
        raise_http(
            422,
            "sourcing_alert.invalid_target",
            "exactly one of part_id or project_id is required",
        )
    if alert_type == "bom_buyable":
        if project_id is None or part_id is not None:
            raise_http(
                422,
                "sourcing_alert.invalid_target",
                "bom_buyable alerts require project_id",
            )
        if country_code is not None or currency_code is not None or distributor_filter:
            raise_http(
                422,
                "sourcing_alert.invalid_scope_filter",
                "bom_buyable alerts cannot use sourcing filters",
            )
    elif part_id is None or project_id is not None:
        raise_http(
            422,
            "sourcing_alert.invalid_target",
            "this alert type requires part_id",
        )

    if part_id is not None:
        assert_in_workspace(db, Part, part_id, workspace.id, label="part")
    if project_id is not None:
        assert_in_workspace(db, Project, project_id, workspace.id, label="project")

    validated_threshold = _validated_threshold(alert_type, threshold)
    validated_notify_user_ids = _validated_notify_user_ids(
        db,
        workspace_id=workspace.id,
        notify_user_ids=notify_user_ids,
    )
    if alert_type not in _SOURCING_FILTER_ALERT_TYPES:
        country_code = None
        currency_code = None
        distributor_filter = None

    return {
        "alert_type": alert_type,
        "part_id": part_id,
        "project_id": project_id,
        "threshold": validated_threshold,
        "country_code": country_code,
        "currency_code": currency_code,
        "distributor_filter": distributor_filter,
        "notify_user_ids": validated_notify_user_ids,
        "cooldown_seconds": cooldown_seconds,
        "enabled": enabled,
    }


def _validated_threshold(
    alert_type: SourcingAlertType | str,
    threshold: dict[str, Any],
) -> dict[str, Any]:
    schema = _THRESHOLD_SCHEMAS[alert_type]
    try:
        parsed = schema.model_validate(threshold)
    except ValidationError as exc:
        raise_http(
            422,
            "sourcing_alert.invalid_threshold",
            "invalid threshold for alert_type",
            errors=json.loads(exc.json()),
        )
    return parsed.model_dump(mode="json")


def _validated_notify_user_ids(
    db: Session,
    *,
    workspace_id: UUID,
    notify_user_ids: list[UUID] | list[str] | None,
) -> list[str] | None:
    if notify_user_ids is None:
        return None
    requested = [UUID(str(user_id)) for user_id in notify_user_ids]
    if not requested:
        return []
    active_members = set(
        db.execute(
            select(WorkspaceMember.user_id).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.status == "active",
                WorkspaceMember.user_id.in_(requested),
            )
        ).scalars()
    )
    missing = [user_id for user_id in requested if user_id not in active_members]
    if missing:
        raise_http(
            404,
            ErrorCodes.WORKSPACE_MEMBER_NOT_FOUND,
            "workspace member not found",
            resource="workspace_member",
        )
    return [str(user_id) for user_id in requested]


def build_purchase_plan(
    db: Session,
    *,
    workspace: Any,
    project: Any,
    build_quantity: int,
    strategy: Strategy = "preferred_first",
    country: str | None = None,
    currency: str | None = None,
    distributors: list[str] | None = None,
    max_distributors: int | None = None,
    moq_overbuy_cap: int | None = None,
    price_tolerance_pct: Decimal = Decimal("5"),
    requested_by: UUID | None = None,
) -> PurchasePlan:
    bom = source_bom(
        db,
        workspace=workspace,
        project=project,
        build_quantity=build_quantity,
        country=country,
        currency=currency,
        distributors=distributors,
        in_stock_only=False,
        use_cached_data=None,
        requested_by=requested_by,
    )
    preferred_distributors = (
        _clean_distributors(distributors)
        if distributors is not None
        else _clean_distributors(workspace.sourcing_preferred_distributors)
    )
    outcome = optimize(
        bom.rows,
        strategy=strategy,
        preferred_distributors=preferred_distributors,
        max_distributors=max_distributors,
        moq_overbuy_cap=moq_overbuy_cap,
        price_tolerance_pct=price_tolerance_pct,
    )
    created_at = utcnow()
    plan = PurchasePlan(
        workspace_id=workspace.id,
        project_id=project.id,
        build_quantity=build_quantity,
        strategy=strategy,
        country_code=_clean_code(country) or workspace.sourcing_country_code,
        currency_code=_clean_code(currency) or workspace.sourcing_currency_code,
        preferred_distributors=preferred_distributors,
        max_distributors=max_distributors,
        moq_overbuy_cap=moq_overbuy_cap,
        price_tolerance_pct=price_tolerance_pct,
        status="draft",
        created_at=created_at,
        expires_at=created_at + PURCHASE_PLAN_TTL,
        created_by=requested_by,
    )
    plan.lines = _purchase_plan_lines_from_selections(outcome.selections, bom.rows)
    db.add(plan)
    db.flush()
    return plan


def _purchase_plan_lines_from_selections(
    selections: Iterable[Any],
    rows: list[SourcingBomLineOut],
) -> list[PurchasePlanLine]:
    rows_by_entry_id = {row.project_entry_id: row for row in rows}
    lines: list[PurchasePlanLine] = []
    for selection in selections:
        source_row = rows_by_entry_id.get(selection.project_entry_id)
        available_offers = (
            [
                offer.model_dump(mode="json")
                for offer in source_row.offers
            ]
            if source_row is not None
            else []
        )
        lines.append(
            PurchasePlanLine(
                project_entry_id=selection.project_entry_id,
                part_id=selection.part_id,
                mpn_searched=selection.mpn_searched,
                required_qty=selection.required_qty,
                internal_available_qty=selection.internal_available_qty,
                shortage_qty=selection.shortage_qty,
                selected_distributor=selection.selected_distributor,
                selected_qty=selection.selected_qty,
                selected_unit_price=selection.selected_unit_price,
                selected_currency=selection.selected_currency,
                selected_packaging=selection.selected_packaging,
                selected_moq=selection.selected_moq,
                selected_lead_time_days=selection.selected_lead_time_days,
                selected_url=selection.selected_url,
                available_offers=available_offers,
                risk_flags=list(selection.risk_flags),
            )
        )
    return lines


def convert_plan_to_orders(
    db: Session,
    *,
    workspace: Any,
    plan: PurchasePlan,
    user_id: UUID | None,
    overrides: dict[UUID, PurchasePlanOrderOverrideIn] | None = None,
) -> list[Order]:
    if plan.status != "refreshed":
        raise PurchasePlanStaleError(
            "plan must be refreshed before conversion; call /refresh first"
        )
    if plan.last_refreshed_at is None:
        raise PurchasePlanStaleError(
            "plan must be refreshed before conversion; call /refresh first"
        )
    if plan.last_refreshed_at < utcnow() - timedelta(seconds=MAX_PLAN_STALENESS_SECONDS):
        raise PurchasePlanStaleError("plan refresh is stale; refresh again before conversion")

    line_updates = _validated_line_updates(plan, overrides or {})
    _validate_line_update_currencies(line_updates)
    _apply_line_updates(line_updates)

    lines_by_distributor: dict[str, list[PurchasePlanLine]] = {}
    display_names: dict[str, str] = {}
    for line in plan.lines:
        if line.selected_distributor is None:
            continue
        key = line.selected_distributor.casefold()
        lines_by_distributor.setdefault(key, []).append(line)
        display_names.setdefault(key, line.selected_distributor)

    orders: list[Order] = []
    for distributor_key in sorted(display_names, key=lambda key: display_names[key].casefold()):
        lines = sorted(
            lines_by_distributor[distributor_key],
            key=lambda line: (str(line.project_entry_id or ""), str(line.id)),
        )
        orders.append(
            _create_order_for_distributor(
                db,
                workspace_id=workspace.id,
                plan=plan,
                distributor=display_names[distributor_key],
                lines=lines,
                user_id=user_id,
            )
        )

    plan.status = "converted"
    db.flush()
    return orders


def _validated_line_updates(
    plan: PurchasePlan,
    overrides: dict[UUID, PurchasePlanOrderOverrideIn],
) -> list[_LineUpdate]:
    lines_by_id = {line.id: line for line in plan.lines}
    unknown_line_ids = sorted(set(overrides) - set(lines_by_id), key=str)
    if unknown_line_ids:
        raise PurchasePlanOverrideError(
            "override line does not belong to this purchase plan"
        )

    updates: list[_LineUpdate] = []
    for line in plan.lines:
        override = overrides.get(line.id)
        if override is None:
            updates.append(_line_update_from_current_selection(line))
            continue
        offer = _matching_cached_offer(line, override)
        updates.append(
            _LineUpdate(
                line=line,
                selected_distributor=offer.distributor,
                selected_qty=override.selected_qty,
                selected_unit_price=override.selected_unit_price,
                selected_currency=override.selected_currency,
                selected_packaging=offer.packaging,
                selected_moq=offer.moq,
                selected_lead_time_days=offer.lead_time_days,
                selected_url=offer.url,
            )
        )
    return updates


def _line_update_from_current_selection(line: PurchasePlanLine) -> _LineUpdate:
    return _LineUpdate(
        line=line,
        selected_distributor=line.selected_distributor,
        selected_qty=line.selected_qty,
        selected_unit_price=line.selected_unit_price,
        selected_currency=line.selected_currency,
        selected_packaging=line.selected_packaging,
        selected_moq=line.selected_moq,
        selected_lead_time_days=line.selected_lead_time_days,
        selected_url=line.selected_url,
    )


def _matching_cached_offer(
    line: PurchasePlanLine,
    override: PurchasePlanOrderOverrideIn,
) -> SourcingBomOfferOut:
    if not line.available_offers:
        raise PurchasePlanStaleError(
            "cached offers are unavailable; refresh again before conversion"
        )
    for raw_offer in line.available_offers:
        offer = SourcingBomOfferOut.model_validate(raw_offer)
        if offer.distributor.casefold() != override.selected_distributor.casefold():
            continue
        if offer.stock < override.selected_qty:
            continue
        if offer.moq is not None and override.selected_qty < offer.moq:
            continue
        if override.selected_qty < line.shortage_qty:
            continue
        if (offer.currency or "").upper() != override.selected_currency:
            continue
        unit_price = _unit_price_for_offer(offer, override.selected_qty)
        if unit_price is None or unit_price != override.selected_unit_price:
            continue
        return offer
    raise PurchasePlanOverrideError(
        "override does not match cached offers for purchase plan line"
    )


def _unit_price_for_offer(offer: SourcingBomOfferOut, qty: int) -> Decimal | None:
    best = best_unit_price_at_qty(offer.price_breaks, qty)
    if best is not None:
        return best[0]
    return offer.unit_price


def _validate_line_update_currencies(updates: list[_LineUpdate]) -> None:
    currencies_by_distributor: dict[str, set[str]] = {}
    display_names: dict[str, str] = {}
    for update in updates:
        if update.selected_distributor is None or update.selected_currency is None:
            continue
        key = update.selected_distributor.casefold()
        currencies_by_distributor.setdefault(key, set()).add(update.selected_currency)
        display_names.setdefault(key, update.selected_distributor)
    for distributor_key, currencies in currencies_by_distributor.items():
        if len(currencies) > 1:
            raise PurchasePlanCurrencyError(
                f"mixed currencies for distributor {display_names[distributor_key]}"
            )


def _apply_line_updates(updates: list[_LineUpdate]) -> None:
    for update in updates:
        update.line.selected_distributor = update.selected_distributor
        update.line.selected_qty = update.selected_qty
        update.line.selected_unit_price = update.selected_unit_price
        update.line.selected_currency = update.selected_currency
        update.line.selected_packaging = update.selected_packaging
        update.line.selected_moq = update.selected_moq
        update.line.selected_lead_time_days = update.selected_lead_time_days
        update.line.selected_url = update.selected_url


def refresh_purchase_plan(
    db: Session,
    *,
    workspace: Any,
    plan: PurchasePlan,
    requested_by: UUID | None = None,
) -> PurchasePlan:
    project = db.execute(
        select(Project).where(Project.id == plan.project_id, Project.workspace_id == workspace.id)
    ).scalar_one_or_none()
    if project is None:
        raise ValueError("purchase plan project not found")

    bom = source_bom(
        db,
        workspace=workspace,
        project=project,
        build_quantity=plan.build_quantity,
        country=plan.country_code,
        currency=plan.currency_code,
        distributors=plan.preferred_distributors,
        in_stock_only=False,
        use_cached_data=False,
        ttl_seconds=0,
        requested_by=requested_by,
        force_refresh=True,
    )
    outcome = optimize(
        bom.rows,
        strategy=plan.strategy,
        preferred_distributors=plan.preferred_distributors,
        max_distributors=plan.max_distributors,
        moq_overbuy_cap=plan.moq_overbuy_cap,
        price_tolerance_pct=Decimal(plan.price_tolerance_pct or Decimal("5")),
    )

    plan.lines = _purchase_plan_lines_from_selections(outcome.selections, bom.rows)
    plan.status = "refreshed"
    plan.last_refreshed_at = utcnow()
    db.flush()
    return plan


def purchase_plan_orders_to_out(
    db: Session,
    *,
    workspace_id: UUID,
    orders: list[Order],
) -> PurchasePlanOrdersOut:
    return PurchasePlanOrdersOut.model_validate({
        "orders": [
            {
                "id": order.id,
                "name": order.name,
                "supplier": order.supplier,
                "status": order.status,
                "currency": order.currency,
                "comments": order.comments,
                "entries": [
                    {
                        "id": entry.id,
                        "part_id": entry.part_id,
                        "quantity_ordered": entry.quantity_ordered,
                        "unit_price": entry.unit_price,
                        "currency": entry.currency,
                        "comments": entry.comments,
                    }
                    for entry in _order_entries(
                        db,
                        workspace_id=workspace_id,
                        order_id=order.id,
                    )
                ],
            }
            for order in orders
        ]
    })


def purchase_plan_to_out(plan: PurchasePlan) -> PurchasePlanOut:
    lines = sorted(
        plan.lines,
        key=lambda line: (
            str(line.project_entry_id or ""),
            (line.selected_distributor or "").casefold(),
            str(line.id),
        ),
    )
    unfilled_count = sum(1 for line in lines if line.selected_distributor is None)
    if unfilled_count:
        est_total_cost: Decimal | None = None
    else:
        est_total_cost = sum(
            (
                line.selected_unit_price * Decimal(line.selected_qty or 0)
                for line in lines
                if line.selected_unit_price is not None
            ),
            Decimal("0"),
        )
    lead_times = [
        line.selected_lead_time_days
        for line in lines
        if line.selected_lead_time_days is not None
    ]
    return PurchasePlanOut.model_validate(
        {
            "id": plan.id,
            "project_id": plan.project_id,
            "build_quantity": plan.build_quantity,
            "strategy": plan.strategy,
            "country_code": plan.country_code,
            "currency_code": plan.currency_code,
            "preferred_distributors": plan.preferred_distributors,
            "max_distributors": plan.max_distributors,
            "moq_overbuy_cap": plan.moq_overbuy_cap,
            "price_tolerance_pct": plan.price_tolerance_pct,
            "status": plan.status,
            "created_at": plan.created_at,
            "expires_at": plan.expires_at,
            "last_refreshed_at": plan.last_refreshed_at,
            "created_by": plan.created_by,
            "lines": lines,
            "distributors_used": sorted(
                {
                    line.selected_distributor
                    for line in lines
                    if line.selected_distributor is not None
                },
                key=str.casefold,
            ),
            "est_total_cost": est_total_cost,
            "worst_lead_time_days": max(lead_times) if lead_times else None,
            "unfilled_count": unfilled_count,
        }
    )


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
    force_refresh: bool = False,
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
            force_refresh=force_refresh,
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
    if any(len(mpn) < 2 or len(mpn) > 100 for mpn in clean_mpns):
        raise ValueError("TrustedParts SearchToken length must be between 2 and 100")

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
                "tp_current_date": (
                    raw.tp_current_date.isoformat()
                    if raw.tp_current_date is not None
                    else None
                ),
                "tp_response_time": raw.tp_response_time,
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
                tp_current_date=raw.tp_current_date,
                tp_response_time=raw.tp_response_time,
                fetched_at=fetched_at,
                cache_hit=cache_hit,
            )
        )

    response_fetched_at = max((result.fetched_at for result in results), default=utcnow())
    request_id = next((result.request_id for result in results if result.request_id), None)
    tp_current_date = max(
        (result.tp_current_date for result in results if result.tp_current_date is not None),
        default=None,
    )
    tp_response_time = next(
        (result.tp_response_time for result in results if result.tp_response_time),
        None,
    )
    return SourcingSearchOut(
        results=results,
        request_id=request_id,
        tp_current_date=tp_current_date,
        tp_response_time=tp_response_time,
        fetched_at=response_fetched_at,
        cache_hit=all(result.cache_hit for result in results),
        links=TRUSTEDPARTS_LINKS,
    )


def _create_order_for_distributor(
    db: Session,
    *,
    workspace_id: UUID,
    plan: PurchasePlan,
    distributor: str,
    lines: list[PurchasePlanLine],
    user_id: UUID | None,
) -> Order:
    currencies = {
        line.selected_currency
        for line in lines
        if line.selected_currency is not None
    }
    if len(currencies) > 1:
        raise PurchasePlanCurrencyError(
            f"mixed currencies for distributor {distributor}"
        )

    today = utcnow().date()
    currency = next(iter(currencies), None)
    order = Order(
        workspace_id=workspace_id,
        name=f"TrustedParts purchase — {distributor} — {today.isoformat()}",
        order_type="purchase",
        supplier=distributor,
        currency=currency,
        status="draft",
        comments=(
            f"TrustedParts purchase plan #{plan.id} — distributor={distributor} "
            f"— generated={today.isoformat()} — strategy={plan.strategy}"
        ),
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(order)
    db.flush()
    for index, line in enumerate(lines):
        db.add(
            OrderEntry(
                workspace_id=workspace_id,
                order_id=order.id,
                part_id=line.part_id,
                quantity_ordered=line.selected_qty or 0,
                unit_price=line.selected_unit_price,
                currency=line.selected_currency,
                comments=(
                    f"TrustedParts: distributor={distributor}, "
                    f"packaging={line.selected_packaging or 'unknown'}, "
                    f"lead_time={_lead_time_label(line.selected_lead_time_days)}, "
                    f"plan={str(plan.id)[:8]}"
                ),
                order_index=index,
                created_by=user_id,
                updated_by=user_id,
            )
        )
    db.flush()
    return order


def _order_entries(
    db: Session,
    *,
    workspace_id: UUID,
    order_id: UUID,
) -> list[OrderEntry]:
    return list(
        db.execute(
            select(OrderEntry)
            .where(OrderEntry.workspace_id == workspace_id)
            .where(OrderEntry.order_id == order_id)
            .order_by(OrderEntry.order_index)
        ).scalars()
    )


def _lead_time_label(value: int | None) -> str:
    if value is None:
        return "unknown"
    return f"{value}d"


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
            "tp_current_date": response.get("tp_current_date"),
            "tp_response_time": response.get("tp_response_time"),
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
    cache_hit = _bom_line_cache_hit(candidate_mpns, search_results)
    reason = _bom_line_reason(candidate_mpns, offers)

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
        cache_hit=cache_hit,
        reason=reason,
        risk_flags=_risk_flags(
            offers,
            best_offer=best_offer,
            short_by=short_by,
            preferred_distributors=preferred_distributors,
        ),
    )


def _bom_line_cache_hit(
    mpns: list[str],
    search_results: dict[str, SourcingSearchResult],
) -> bool | None:
    if not mpns:
        return None
    results = [
        result
        for mpn in mpns
        if (result := search_results.get(mpn.casefold())) is not None
    ]
    if not results:
        return None
    return all(result.cache_hit for result in results)


def _bom_line_reason(
    mpns: list[str],
    offers: list[SourcingBomOfferOut],
) -> str:
    if not mpns:
        return "no_mpn"
    if not offers:
        return "no_offers"
    return "ok"


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
                        lifecycle_risk=offer.lifecycle_risk,
                        supply_chain_risk=offer.supply_chain_risk,
                        is_affected_by_tariff=offer.is_affected_by_tariff,
                        rohs_compliance=distributor.rohs_compliance,
                        manufacturer_id=offer.manufacturer_id,
                        specifications=offer.specifications,
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
    flags.extend(_gap_field_risk_flags(offers))
    return flags


def _gap_field_risk_flags(offers: list[SourcingBomOfferOut]) -> list[str]:
    flags: list[str] = []
    if any(_has_text(offer.lifecycle_risk) for offer in offers):
        flags.append("lifecycle_risk_present")
    if any(_has_text(offer.supply_chain_risk) for offer in offers):
        flags.append("supply_chain_risk_present")
    if any(offer.is_affected_by_tariff is True for offer in offers):
        flags.append("tariff_affected")
    # Workspace-level RoHS target regions do not exist yet; default to EU for TPS-5.
    if offers and all(not _has_compliant_rohs_region(offer) for offer in offers):
        flags.append("rohs_non_compliant")
    return flags


def _has_text(value: str | None) -> bool:
    return value is not None and value.strip() != ""


def _has_compliant_rohs_region(offer: SourcingBomOfferOut) -> bool:
    target = TARGET_ROHS_REGION.casefold()
    return any(
        item.region.casefold() == target and item.is_compliant is True
        for item in offer.rohs_compliance
    )


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
