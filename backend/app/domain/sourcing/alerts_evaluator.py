"""Evaluate sourcing alerts and dispatch email notifications."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mail
from app.core.config import settings
from app.core.time import utcnow
from app.domain.parts.models import Part
from app.domain.projects.models import Project
from app.domain.sourcing import service as sourcing_service
from app.domain.sourcing.pricing import best_unit_price_at_qty
from app.domain.sourcing.schemas import (
    BomBuyableThreshold,
    PriceChangedThreshold,
    StockAboveThreshold,
    StockBelowThreshold,
    StringChangedThreshold,
)
from app.domain.stock import service as stock_service
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace, WorkspaceMember

from .models import SourcingAlert

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlertVerdict:
    triggered: bool
    summary: str
    detail: dict[str, Any]
    new_state: dict[str, Any]


@dataclass(frozen=True)
class AlertEmail:
    recipients: list[str]
    subject: str
    body: str


EvaluatorFn = Callable[[Session, Workspace, SourcingAlert], AlertVerdict]


def evaluate_all_alerts(db: Session) -> int:
    """Evaluate every enabled, non-archived sourcing alert.

    Returns the number of alert rows attempted. Each row commits
    independently so a bad alert cannot roll back earlier bookkeeping.
    """
    rows = db.execute(
        select(SourcingAlert, Workspace)
        .join(Workspace, Workspace.id == SourcingAlert.workspace_id)
        .where(SourcingAlert.enabled.is_(True))
        .where(SourcingAlert.archived_at.is_(None))
        .order_by(SourcingAlert.workspace_id, SourcingAlert.created_at, SourcingAlert.id)
    ).all()

    evaluated = 0
    for alert, workspace in rows:
        evaluated += 1
        alert_id: UUID | None = None
        alert_type: str | None = None
        workspace_id: UUID | None = None
        try:
            alert_id = alert.id
            alert_type = alert.alert_type
            workspace_id = workspace.id
            evaluator = EVALUATORS[alert.alert_type]
            verdict = evaluator(db, workspace, alert)
            checked_at = utcnow()
            alert.last_checked_at = checked_at
            alert.last_evaluation_state = verdict.new_state
            sent = False
            email: AlertEmail | None = None
            if verdict.triggered and _cooldown_elapsed(alert):
                email = _prepare_alert_email(db, workspace, alert, verdict)
                if email is not None:
                    alert.last_notified_at = checked_at
            db.commit()
            if email is not None:
                sent = _send_alert_email(
                    workspace_id=workspace_id,
                    alert_id=alert_id,
                    email=email,
                )
            log.info(
                "sourcing_alert.evaluated workspace_id=%s alert_id=%s type=%s "
                "triggered=%s notified=%s",
                workspace_id,
                alert_id,
                alert_type,
                verdict.triggered,
                sent,
            )
        except Exception:
            db.rollback()
            log.exception(
                "sourcing_alert.evaluation_failed workspace_id=%s alert_id=%s type=%s",
                workspace_id,
                alert_id,
                alert_type,
            )
    return evaluated


def _evaluate_stock_below(
    db: Session,
    workspace: Workspace,
    alert: SourcingAlert,
) -> AlertVerdict:
    part = _part_for_alert(db, workspace, alert)
    qty = stock_service.current_quantity(
        db,
        workspace_id=workspace.id,
        part_id=part.id,
    )
    threshold = _threshold_model(alert, StockBelowThreshold).qty
    previous_qty = _previous_int(alert.last_evaluation_state, "qty")
    triggered = previous_qty is not None and previous_qty >= threshold and qty < threshold
    return AlertVerdict(
        triggered=triggered,
        summary=f"{part.name} stock below {threshold}",
        detail={
            "part_id": str(part.id),
            "part_name": part.name,
            "mpn": part.mpn,
            "current_qty": qty,
            "threshold_qty": threshold,
        },
        new_state={"qty": qty},
    )


def _evaluate_stock_above(
    db: Session,
    workspace: Workspace,
    alert: SourcingAlert,
) -> AlertVerdict:
    part = _part_for_alert(db, workspace, alert)
    qty = stock_service.current_quantity(
        db,
        workspace_id=workspace.id,
        part_id=part.id,
    )
    threshold = _threshold_model(alert, StockAboveThreshold).qty
    previous_qty = _previous_int(alert.last_evaluation_state, "qty")
    triggered = previous_qty is not None and previous_qty <= threshold and qty > threshold
    return AlertVerdict(
        triggered=triggered,
        summary=f"{part.name} stock above {threshold}",
        detail={
            "part_id": str(part.id),
            "part_name": part.name,
            "mpn": part.mpn,
            "current_qty": qty,
            "threshold_qty": threshold,
        },
        new_state={"qty": qty},
    )


def _evaluate_back_in_stock(
    db: Session,
    workspace: Workspace,
    alert: SourcingAlert,
) -> AlertVerdict:
    part = _part_for_alert(db, workspace, alert)
    authorized_stock = _authorized_stock_for_part(db, workspace, alert, part)
    had_stock = authorized_stock > 0
    previous_had_stock = _previous_bool(alert.last_evaluation_state, "had_stock")
    triggered = previous_had_stock is False and had_stock
    return AlertVerdict(
        triggered=triggered,
        summary=f"{part.name} is back in authorized stock",
        detail={
            "part_id": str(part.id),
            "part_name": part.name,
            "mpn": part.mpn,
            "authorized_stock": authorized_stock,
        },
        new_state={"had_stock": had_stock},
    )


def _evaluate_out_of_authorized_stock(
    db: Session,
    workspace: Workspace,
    alert: SourcingAlert,
) -> AlertVerdict:
    part = _part_for_alert(db, workspace, alert)
    authorized_stock = _authorized_stock_for_part(db, workspace, alert, part)
    had_stock = authorized_stock > 0
    previous_had_stock = _previous_bool(alert.last_evaluation_state, "had_stock")
    triggered = previous_had_stock is True and not had_stock
    return AlertVerdict(
        triggered=triggered,
        summary=f"{part.name} is out of authorized stock",
        detail={
            "part_id": str(part.id),
            "part_name": part.name,
            "mpn": part.mpn,
            "authorized_stock": authorized_stock,
        },
        new_state={"had_stock": had_stock},
    )


def _evaluate_price_changed(
    db: Session,
    workspace: Workspace,
    alert: SourcingAlert,
) -> AlertVerdict:
    part = _part_for_alert(db, workspace, alert)
    best_price = _best_price_for_part(db, workspace, alert, part)
    if best_price is None:
        return AlertVerdict(
            triggered=False,
            summary=f"{part.name} has no authorized price",
            detail={
                "part_id": str(part.id),
                "part_name": part.name,
                "mpn": part.mpn,
                "current_price": None,
                "currency": None,
            },
            new_state={"last_price": None, "last_currency": None},
        )

    current_price, current_currency = best_price
    previous = alert.last_evaluation_state or {}
    previous_price = _decimal_or_none(previous.get("last_price"))
    previous_currency = previous.get("last_currency")
    delta_pct = _threshold_model(alert, PriceChangedThreshold).delta_pct
    triggered = False
    observed_delta: Decimal | None = None
    if (
        previous_price is not None
        and previous_price != 0
        and previous_currency == current_currency
    ):
        observed_delta = abs((current_price - previous_price) / previous_price) * 100
        triggered = observed_delta >= delta_pct

    return AlertVerdict(
        triggered=triggered,
        summary=f"{part.name} authorized price changed",
        detail={
            "part_id": str(part.id),
            "part_name": part.name,
            "mpn": part.mpn,
            "previous_price": str(previous_price) if previous_price is not None else None,
            "current_price": str(current_price),
            "currency": current_currency,
            "delta_pct": str(observed_delta) if observed_delta is not None else None,
            "threshold_delta_pct": str(delta_pct),
        },
        new_state={
            "last_price": str(current_price),
            "last_currency": current_currency,
        },
    )


def _evaluate_bom_buyable(
    db: Session,
    workspace: Workspace,
    alert: SourcingAlert,
) -> AlertVerdict:
    project = _project_for_alert(db, workspace, alert)
    build_quantity = _threshold_model(alert, BomBuyableThreshold).build_quantity
    bom = sourcing_service.source_bom(
        db,
        workspace=workspace,
        project=project,
        build_quantity=build_quantity,
        country=alert.country_code,
        currency=alert.currency_code,
        distributors=_distributor_filter(alert),
        use_cached_data=True,
    )
    is_buyable = bom.capacity.can_build_after_purchase >= build_quantity
    previous_is_buyable = _previous_bool(alert.last_evaluation_state, "is_buyable")
    triggered = previous_is_buyable is False and is_buyable
    return AlertVerdict(
        triggered=triggered,
        summary=f"{project.name} BOM is buyable",
        detail={
            "project_id": str(project.id),
            "project_name": project.name,
            "build_quantity": build_quantity,
            "can_build_after_purchase": bom.capacity.can_build_after_purchase,
        },
        new_state={"is_buyable": is_buyable},
    )


def _evaluate_lifecycle_risk_changed(
    db: Session,
    workspace: Workspace,
    alert: SourcingAlert,
) -> AlertVerdict:
    part = _part_for_alert(db, workspace, alert)
    current = _gap_field_for_part(db, workspace, alert, part, "lifecycle_risk")
    return _string_changed_verdict(
        alert=alert,
        part=part,
        current=current,
        state_key="lifecycle_risk",
        label="lifecycle risk",
    )


def _evaluate_supply_chain_risk_changed(
    db: Session,
    workspace: Workspace,
    alert: SourcingAlert,
) -> AlertVerdict:
    part = _part_for_alert(db, workspace, alert)
    current = _gap_field_for_part(db, workspace, alert, part, "supply_chain_risk")
    return _string_changed_verdict(
        alert=alert,
        part=part,
        current=current,
        state_key="supply_chain_risk",
        label="supply-chain risk",
    )


def _evaluate_tariff_status_changed(
    db: Session,
    workspace: Workspace,
    alert: SourcingAlert,
) -> AlertVerdict:
    part = _part_for_alert(db, workspace, alert)
    current = _gap_field_for_part(db, workspace, alert, part, "is_affected_by_tariff")
    previous_state = alert.last_evaluation_state
    previous = previous_state.get("tariff") if previous_state is not None else None
    triggered = previous_state is not None and current != previous
    return AlertVerdict(
        triggered=triggered,
        summary=f"{part.name} tariff status changed",
        detail={
            "part_id": str(part.id),
            "part_name": part.name,
            "mpn": part.mpn,
            "from": previous,
            "to": current,
        },
        new_state={"tariff": current},
    )


EVALUATORS: dict[str, EvaluatorFn] = {
    "stock_below": _evaluate_stock_below,
    "stock_above": _evaluate_stock_above,
    "back_in_stock": _evaluate_back_in_stock,
    "out_of_authorized_stock": _evaluate_out_of_authorized_stock,
    "price_changed": _evaluate_price_changed,
    "bom_buyable": _evaluate_bom_buyable,
    "lifecycle_risk_changed": _evaluate_lifecycle_risk_changed,
    "supply_chain_risk_changed": _evaluate_supply_chain_risk_changed,
    "tariff_status_changed": _evaluate_tariff_status_changed,
}


def _cooldown_elapsed(alert: SourcingAlert) -> bool:
    if alert.last_notified_at is None:
        return True
    return (utcnow() - alert.last_notified_at).total_seconds() >= alert.cooldown_seconds


def _send_alert_email(
    *,
    workspace_id: UUID,
    alert_id: UUID,
    email: AlertEmail,
) -> bool:
    sent_any = False
    for recipient in email.recipients:
        try:
            mail.send(to=recipient, subject=email.subject, text_body=email.body)
            sent_any = True
        except Exception as exc:
            log.warning(
                "sourcing_alert.smtp_failed workspace_id=%s alert_id=%s to=%s "
                "exception_type=%s error=%s",
                workspace_id,
                alert_id,
                recipient,
                type(exc).__name__,
                exc,
            )
            log.debug(
                "sourcing_alert.smtp_failed_debug workspace_id=%s alert_id=%s to=%s",
                workspace_id,
                alert_id,
                recipient,
                exc_info=True,
            )
    return sent_any


def _prepare_alert_email(
    db: Session,
    workspace: Workspace,
    alert: SourcingAlert,
    verdict: AlertVerdict,
) -> AlertEmail | None:
    recipients = _recipient_emails(db, workspace, alert)
    if not recipients:
        log.warning(
            "sourcing_alert.no_recipients workspace_id=%s alert_id=%s",
            workspace.id,
            alert.id,
        )
        return None

    subject = f"[stockManager] {verdict.summary}"
    body = _render_email_body(workspace, alert, verdict)
    return AlertEmail(recipients=recipients, subject=subject, body=body)


def _recipient_emails(
    db: Session,
    workspace: Workspace,
    alert: SourcingAlert,
) -> list[str]:
    base = (
        select(User.email)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .where(WorkspaceMember.workspace_id == workspace.id)
        .where(WorkspaceMember.status == "active")
    )
    if alert.notify_user_ids is None:
        query = base.where(WorkspaceMember.role.in_(("owner", "admin")))
    else:
        user_ids = [UUID(str(item)) for item in alert.notify_user_ids]
        if not user_ids:
            return []
        query = base.where(User.id.in_(user_ids))
    return list(db.execute(query.order_by(User.email)).scalars())


def _render_email_body(
    workspace: Workspace,
    alert: SourcingAlert,
    verdict: AlertVerdict,
) -> str:
    cfg = settings()
    target_path = (
        f"/parts/{alert.part_id}" if alert.part_id is not None else f"/projects/{alert.project_id}"
    )
    manage_path = f"/sourcing/alerts?alert_id={alert.id}"
    detail_lines = "\n".join(
        f"- {key}: {value}" for key, value in sorted(verdict.detail.items())
    )
    return (
        f"{verdict.summary}\n\n"
        f"Workspace: {workspace.name}\n"
        f"Alert type: {alert.alert_type}\n\n"
        f"Details:\n{detail_lines}\n\n"
        f"Open target: {cfg.APP_BASE_URL}{target_path}\n"
        f"Manage this alert: {cfg.APP_BASE_URL}{manage_path}\n\n"
        f"You are receiving this because you are configured as a recipient "
        f"for this stockManager sourcing alert."
    )


def _part_for_alert(db: Session, workspace: Workspace, alert: SourcingAlert) -> Part:
    if alert.part_id is None:
        raise ValueError(f"alert {alert.id} has no part_id")
    part = db.execute(
        select(Part)
        .where(Part.workspace_id == workspace.id)
        .where(Part.id == alert.part_id)
        .where(Part.archived_at.is_(None))
    ).scalar_one_or_none()
    if part is None:
        raise ValueError(f"alert {alert.id} part is missing from workspace {workspace.id}")
    return part


def _project_for_alert(db: Session, workspace: Workspace, alert: SourcingAlert) -> Project:
    if alert.project_id is None:
        raise ValueError(f"alert {alert.id} has no project_id")
    project = db.execute(
        select(Project)
        .where(Project.workspace_id == workspace.id)
        .where(Project.id == alert.project_id)
        .where(Project.archived_at.is_(None))
    ).scalar_one_or_none()
    if project is None:
        raise ValueError(f"alert {alert.id} project is missing from workspace {workspace.id}")
    return project


def _authorized_stock_for_part(
    db: Session,
    workspace: Workspace,
    alert: SourcingAlert,
    part: Part,
) -> int:
    if not part.mpn:
        return 0
    result = sourcing_service.search(
        db,
        workspace=workspace,
        mpns=[part.mpn],
        country=alert.country_code,
        currency=alert.currency_code,
        distributors=_distributor_filter(alert),
        use_cached_data=True,
    )
    return sum(
        max(0, int(distributor.stock or 0))
        for item in result.results
        for offer in item.offers
        for distributor in offer.distributors
    )


def _best_price_for_part(
    db: Session,
    workspace: Workspace,
    alert: SourcingAlert,
    part: Part,
) -> tuple[Decimal, str | None] | None:
    if not part.mpn:
        return None
    result = sourcing_service.search(
        db,
        workspace=workspace,
        mpns=[part.mpn],
        country=alert.country_code,
        currency=alert.currency_code,
        distributors=_distributor_filter(alert),
        use_cached_data=True,
    )
    best: tuple[Decimal, str | None] | None = None
    for item in result.results:
        for offer in item.offers:
            for distributor in offer.distributors:
                candidate = _unit_price_for_distributor(distributor)
                if candidate is None:
                    continue
                price, currency = candidate
                if best is None or price < best[0]:
                    best = (price, currency)
    return best


def _gap_field_for_part(
    db: Session,
    workspace: Workspace,
    alert: SourcingAlert,
    part: Part,
    field_name: str,
) -> Any:
    offer = _first_matching_offer(db, workspace, alert, part)
    if offer is None:
        return None
    return getattr(offer, field_name, None)


def _first_matching_offer(
    db: Session,
    workspace: Workspace,
    alert: SourcingAlert,
    part: Part,
) -> Any | None:
    if not part.mpn:
        return None
    result = sourcing_service.search(
        db,
        workspace=workspace,
        mpns=[part.mpn],
        country=alert.country_code,
        currency=alert.currency_code,
        distributors=_distributor_filter(alert),
        use_cached_data=True,
    )
    expected = part.mpn.casefold()
    for item in result.results:
        item_mpn = getattr(item, "mpn", None)
        if item_mpn is not None and str(item_mpn).casefold() != expected:
            continue
        for offer in item.offers:
            offer_mpn = getattr(offer, "mpn", None)
            if offer_mpn is None or str(offer_mpn).casefold() == expected:
                return offer
    return None


def _string_changed_verdict(
    *,
    alert: SourcingAlert,
    part: Part,
    current: str | None,
    state_key: str,
    label: str,
) -> AlertVerdict:
    previous_state = alert.last_evaluation_state
    previous = previous_state.get(state_key) if previous_state is not None else None
    triggered = previous_state is not None and current != previous
    if triggered and not _string_threshold_matches(alert, current):
        triggered = False
    return AlertVerdict(
        triggered=triggered,
        summary=f"{part.name} {label} changed",
        detail={
            "part_id": str(part.id),
            "part_name": part.name,
            "mpn": part.mpn,
            "from": previous,
            "to": current,
        },
        new_state={state_key: current},
    )


def _string_threshold_matches(alert: SourcingAlert, current: str | None) -> bool:
    threshold = _threshold_model(alert, StringChangedThreshold)
    must_contain = threshold.must_contain
    if must_contain in (None, ""):
        return True
    if current is None:
        return False
    needle = str(must_contain)
    haystack = str(current)
    if not threshold.case_sensitive:
        needle = needle.casefold()
        haystack = haystack.casefold()
    return needle in haystack


def _unit_price_for_distributor(distributor: Any) -> tuple[Decimal, str | None] | None:
    price_breaks = distributor.price_breaks_converted or distributor.price_breaks
    best = best_unit_price_at_qty(price_breaks, 1)
    if best is not None:
        return best[0], distributor.currency_displayed or distributor.currency
    price = distributor.unit_price_converted
    currency = distributor.currency_displayed
    if price is None:
        price = distributor.unit_price
        currency = distributor.currency
    if price is None:
        return None
    return Decimal(str(price)), currency


def _distributor_filter(alert: SourcingAlert) -> list[str] | None:
    if alert.distributor_filter is None:
        return None
    return [str(item).strip() for item in alert.distributor_filter if str(item).strip()]


def _threshold_model[T: BaseModel](alert: SourcingAlert, schema: type[T]) -> T:
    try:
        return schema.model_validate(alert.threshold or {})
    except ValidationError as exc:
        raise ValueError(f"alert {alert.id} has invalid {schema.__name__}") from exc


def _previous_int(state: dict[str, Any] | None, key: str) -> int | None:
    if not state or state.get(key) is None:
        return None
    try:
        return int(state[key])
    except (TypeError, ValueError):
        return None


def _previous_bool(state: dict[str, Any] | None, key: str) -> bool | None:
    if not state or state.get(key) is None:
        return None
    value = state[key]
    if isinstance(value, bool):
        return value
    return None


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None
