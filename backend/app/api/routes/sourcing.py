"""Sourcing provider endpoints."""
from __future__ import annotations

from decimal import Decimal
from time import perf_counter
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api._helpers import assert_in_workspace
from app.core.deps import CurrentUser, CurrentWorkspace, require_role
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import err, ok
from app.core.time import utcnow
from app.domain.fx import rates as fx_rates
from app.domain.fx._apply import apply_fx_to_offer
from app.domain.parts.models import Part
from app.domain.projects.models import Project
from app.domain.sourcing import (
    SourcingAuthError,
    SourcingClientError,
    SourcingRateLimitError,
    SourcingTimeoutError,
)
from app.domain.sourcing import service as sourcing_service
from app.domain.sourcing.factory import make_sourcing_provider
from app.domain.sourcing.models import PurchasePlan
from app.domain.sourcing.schemas import (
    PurchasePlanIn,
    PurchasePlanOrdersIn,
    SourcingAlertIn,
    SourcingAlertOut,
    SourcingAlertPatch,
    SourcingAlertType,
    SourcingBomIn,
    SourcingQuery,
    SourcingSearchIn,
)
from app.domain.sourcing.service import SourcingBudgetBlocked, SourcingNotConfigured
from app.infra.db import get_db

router = APIRouter()
search_router = APIRouter()
parts_router = APIRouter()
projects_router = APIRouter()

_TEST_PROBE_TOKEN = "TEST_PROBE_DO_NOT_BUY"
_PART_SOURCING_TTL_SECONDS = 1800


def _clean_currency(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().upper()
    return cleaned or None


def _elapsed_ms(started_at: float) -> int:
    return max(1, int((perf_counter() - started_at) * 1000))


def _test_result(is_ok: bool, message: str, latency_ms: int):
    return ok({"ok": is_ok, "message": message, "latency_ms": latency_ms})


@router.post(
    "/current/sourcing/test",
    dependencies=[Depends(require_role("admin"))],
)
@limiter.limit("6/minute", key_func=workspace_key)
def test_sourcing_connection(request: Request, ws: CurrentWorkspace):
    started_at = perf_counter()
    client = make_sourcing_provider(ws)
    if client is None:
        return _test_result(False, "not configured", 0)

    try:
        client.search(
            [SourcingQuery(search_token=_TEST_PROBE_TOKEN)],
            use_cached_data=False,
        )
    except SourcingAuthError:
        return _test_result(False, "invalid credentials", _elapsed_ms(started_at))
    except SourcingRateLimitError:
        return _test_result(False, "rate limited by TrustedParts", _elapsed_ms(started_at))
    except SourcingTimeoutError:
        return _test_result(False, "timeout reaching TrustedParts", _elapsed_ms(started_at))
    except SourcingClientError:
        return _test_result(False, "TrustedParts upstream error", _elapsed_ms(started_at))

    return _test_result(True, "OK", _elapsed_ms(started_at))


@search_router.post(
    "/search",
    dependencies=[Depends(require_role("member"))],
)
@limiter.limit("60/minute", key_func=workspace_key)
def search_sourcing(
    request: Request,
    payload: SourcingSearchIn,
    ws: CurrentWorkspace,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    try:
        out = sourcing_service.search(
            db,
            workspace=ws,
            mpns=payload.mpns,
            country=payload.country,
            currency=payload.currency,
            in_stock_only=payload.in_stock_only,
            distributors=payload.distributors,
            use_cached_data=payload.use_cached_data,
            requested_by=user.id,
        )
    except SourcingNotConfigured:
        return _error_response(request, 409, "conflict", "sourcing not configured")
    except SourcingBudgetBlocked:
        return _error_response(request, 503, "server_error", "sourcing budget exhausted")
    except SourcingAuthError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts rejected sourcing credentials",
        )
    except SourcingRateLimitError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts rate limit reached",
        )
    except SourcingTimeoutError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts request timed out",
        )
    except ValueError as exc:
        return _error_response(request, 422, "validation_error", str(exc))
    except SourcingClientError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts sourcing request failed",
        )

    return ok(out.model_dump(mode="json"))


@search_router.get(
    "/alerts",
    dependencies=[Depends(require_role("member"))],
)
def list_sourcing_alerts(
    ws: CurrentWorkspace,
    enabled: bool | None = None,
    alert_type: SourcingAlertType | None = None,
    part_id: UUID | None = None,
    project_id: UUID | None = None,
    include_archived: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    alerts = sourcing_service.list_alerts(
        db,
        workspace=ws,
        enabled=enabled,
        alert_type=alert_type,
        part_id=part_id,
        project_id=project_id,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )
    return ok([_alert_payload(alert) for alert in alerts])


@search_router.post(
    "/alerts",
    dependencies=[Depends(require_role("member"))],
)
@limiter.limit("30/minute", key_func=workspace_key)
def create_sourcing_alert(
    request: Request,
    payload: SourcingAlertIn,
    ws: CurrentWorkspace,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    alert = sourcing_service.create_alert(
        db,
        workspace=ws,
        user_id=user.id,
        payload=payload,
    )
    return ok(_alert_payload(alert))


@search_router.get(
    "/alerts/{alert_id}",
    dependencies=[Depends(require_role("member"))],
)
def get_sourcing_alert(
    alert_id: UUID,
    ws: CurrentWorkspace,
    db: Session = Depends(get_db),
):
    alert = sourcing_service.get_alert(db, workspace=ws, alert_id=alert_id)
    return ok(_alert_payload(alert))


@search_router.patch(
    "/alerts/{alert_id}",
    dependencies=[Depends(require_role("member"))],
)
def update_sourcing_alert(
    alert_id: UUID,
    payload: SourcingAlertPatch,
    ws: CurrentWorkspace,
    db: Session = Depends(get_db),
):
    alert = sourcing_service.update_alert(
        db,
        workspace=ws,
        alert_id=alert_id,
        payload=payload,
    )
    return ok(_alert_payload(alert))


@search_router.delete(
    "/alerts/{alert_id}",
    dependencies=[Depends(require_role("member"))],
)
def delete_sourcing_alert(
    alert_id: UUID,
    ws: CurrentWorkspace,
    db: Session = Depends(get_db),
):
    alert = sourcing_service.archive_alert(db, workspace=ws, alert_id=alert_id)
    return ok(_alert_payload(alert))


@projects_router.post(
    "/{project_id}/sourcing",
    dependencies=[Depends(require_role("member"))],
)
@limiter.limit("30/minute", key_func=workspace_key)
def source_project_bom(
    request: Request,
    project_id: UUID,
    payload: SourcingBomIn,
    ws: CurrentWorkspace,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    project = assert_in_workspace(db, Project, project_id, ws.id, label="project")
    try:
        out = sourcing_service.source_bom(
            db,
            workspace=ws,
            project=project,
            build_quantity=payload.build_quantity,
            country=payload.country,
            currency=payload.currency,
            distributors=payload.distributors,
            in_stock_only=payload.in_stock_only,
            use_cached_data=payload.use_cached_data,
            requested_by=user.id,
        )
    except SourcingNotConfigured:
        return _error_response(request, 409, "conflict", "sourcing not configured")
    except SourcingBudgetBlocked:
        return _error_response(request, 503, "server_error", "sourcing budget exhausted")
    except SourcingAuthError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts rejected sourcing credentials",
        )
    except SourcingRateLimitError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts rate limit reached",
        )
    except SourcingTimeoutError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts request timed out",
        )
    except ValueError as exc:
        return _error_response(request, 422, "validation_error", str(exc))
    except SourcingClientError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts sourcing request failed",
        )

    return ok(out.model_dump(mode="json"))


@projects_router.post(
    "/{project_id}/purchase-plan",
    dependencies=[Depends(require_role("member"))],
)
@limiter.limit("15/minute", key_func=workspace_key)
def create_project_purchase_plan(
    request: Request,
    project_id: UUID,
    payload: PurchasePlanIn,
    ws: CurrentWorkspace,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    project = assert_in_workspace(db, Project, project_id, ws.id, label="project")
    try:
        plan = sourcing_service.build_purchase_plan(
            db,
            workspace=ws,
            project=project,
            build_quantity=payload.build_quantity,
            strategy=payload.strategy,
            country=payload.country,
            currency=payload.currency,
            distributors=payload.distributors,
            max_distributors=payload.max_distributors,
            moq_overbuy_cap=payload.moq_overbuy_cap,
            price_tolerance_pct=payload.price_tolerance_pct,
            requested_by=user.id,
        )
    except SourcingNotConfigured:
        return _error_response(request, 409, "conflict", "sourcing not configured")
    except SourcingBudgetBlocked:
        return _error_response(request, 503, "server_error", "sourcing budget exhausted")
    except SourcingAuthError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts rejected sourcing credentials",
        )
    except SourcingRateLimitError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts rate limit reached",
        )
    except SourcingTimeoutError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts request timed out",
        )
    except ValueError as exc:
        return _error_response(request, 422, "validation_error", str(exc))
    except SourcingClientError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts sourcing request failed",
        )

    return ok(sourcing_service.purchase_plan_to_out(plan).model_dump(mode="json"))


@projects_router.get(
    "/{project_id}/purchase-plans/{plan_id}",
    dependencies=[Depends(require_role("member"))],
)
def get_project_purchase_plan(
    request: Request,
    project_id: UUID,
    plan_id: UUID,
    ws: CurrentWorkspace,
    db: Session = Depends(get_db),
):
    project = assert_in_workspace(db, Project, project_id, ws.id, label="project")
    plan = assert_in_workspace(db, PurchasePlan, plan_id, ws.id, label="purchase plan")
    if plan.project_id != project.id:
        return _error_response(request, 404, "not_found", "purchase plan not found")

    return ok(sourcing_service.purchase_plan_to_out(plan).model_dump(mode="json"))


@search_router.post(
    "/purchase-plans/{plan_id}/refresh",
    dependencies=[Depends(require_role("member"))],
)
@limiter.limit("15/minute", key_func=workspace_key)
def refresh_purchase_plan(
    request: Request,
    plan_id: UUID,
    ws: CurrentWorkspace,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    plan = assert_in_workspace(db, PurchasePlan, plan_id, ws.id, label="purchase plan")
    if plan.expires_at <= utcnow():
        return _error_response(request, 409, "conflict", "plan expired")

    try:
        refreshed = sourcing_service.refresh_purchase_plan(
            db,
            workspace=ws,
            plan=plan,
            requested_by=user.id,
        )
    except SourcingNotConfigured:
        return _error_response(request, 409, "conflict", "sourcing not configured")
    except SourcingBudgetBlocked:
        return _error_response(request, 503, "server_error", "sourcing budget exhausted")
    except SourcingAuthError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts rejected sourcing credentials",
        )
    except SourcingRateLimitError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts rate limit reached",
        )
    except SourcingTimeoutError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts request timed out",
        )
    except ValueError as exc:
        return _error_response(request, 422, "validation_error", str(exc))
    except SourcingClientError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts sourcing request failed",
        )

    return ok(sourcing_service.purchase_plan_to_out(refreshed).model_dump(mode="json"))


@search_router.post(
    "/purchase-plans/{plan_id}/orders",
    dependencies=[Depends(require_role("member"))],
)
def convert_purchase_plan_to_orders(
    request: Request,
    plan_id: UUID,
    ws: CurrentWorkspace,
    user: CurrentUser,
    db: Session = Depends(get_db),
    payload: PurchasePlanOrdersIn | None = None,
):
    plan = assert_in_workspace(db, PurchasePlan, plan_id, ws.id, label="purchase plan")
    try:
        orders = sourcing_service.convert_plan_to_orders(
            db,
            workspace=ws,
            plan=plan,
            user_id=user.id,
            overrides=(payload.overrides if payload is not None else None),
        )
    except sourcing_service.PurchasePlanStaleError as exc:
        return _error_response(request, 409, "conflict", str(exc))
    except sourcing_service.PurchasePlanOverrideError as exc:
        return _error_response(request, 422, "validation_error", str(exc))
    except sourcing_service.PurchasePlanCurrencyError as exc:
        return _error_response(request, 422, "validation_error", str(exc))

    return ok(
        sourcing_service.purchase_plan_orders_to_out(
            db,
            workspace_id=ws.id,
            orders=orders,
        ).model_dump(mode="json")
    )


@parts_router.get(
    "/{part_id}/sourcing",
    dependencies=[Depends(require_role("member"))],
)
@limiter.limit("60/minute", key_func=workspace_key)
def get_part_sourcing(
    request: Request,
    part_id: UUID,
    ws: CurrentWorkspace,
    user: CurrentUser,
    country: str | None = Query(default=None, min_length=2, max_length=2),
    currency: str | None = Query(default=None, min_length=3, max_length=3),
    in_stock_only: bool = False,
    distributors: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
):
    part = assert_in_workspace(db, Part, part_id, ws.id, label="part")
    mpn = (part.mpn or "").strip()
    if not mpn:
        return ok({"offers": [], "reason": "no_mpn", "cache_hit": None})

    try:
        out = sourcing_service.search(
            db,
            workspace=ws,
            mpns=[mpn],
            country=country,
            currency=currency,
            in_stock_only=in_stock_only,
            distributors=_clean_query_distributors(distributors),
            use_cached_data=ws.sourcing_use_cached_for_dashboards,
            ttl_seconds=_PART_SOURCING_TTL_SECONDS,
            requested_by=user.id,
        )
    except SourcingNotConfigured:
        return _error_response(request, 409, "conflict", "sourcing not configured")
    except SourcingBudgetBlocked:
        return _error_response(request, 503, "server_error", "sourcing budget exhausted")
    except SourcingAuthError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts rejected sourcing credentials",
        )
    except SourcingRateLimitError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts rate limit reached",
        )
    except SourcingTimeoutError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts request timed out",
        )
    except ValueError as exc:
        return _error_response(request, 422, "validation_error", str(exc))
    except SourcingClientError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts sourcing request failed",
        )

    return ok(_part_sourcing_payload(out, db=db, requested_currency=currency))


@parts_router.post(
    "/{part_id}/sourcing/refresh",
    dependencies=[Depends(require_role("member"))],
)
@limiter.limit("6/minute", key_func=workspace_key)
def refresh_part_sourcing(
    request: Request,
    part_id: UUID,
    ws: CurrentWorkspace,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    part = assert_in_workspace(db, Part, part_id, ws.id, label="part")
    mpn = (part.mpn or "").strip()
    if not mpn:
        return _error_response(request, 422, "validation_error", "part has no MPN")

    try:
        out = sourcing_service.search(
            db,
            workspace=ws,
            mpns=[mpn],
            use_cached_data=False,
            ttl_seconds=_PART_SOURCING_TTL_SECONDS,
            requested_by=user.id,
            force_refresh=True,
        )
    except SourcingNotConfigured:
        return _error_response(request, 409, "conflict", "sourcing not configured")
    except SourcingBudgetBlocked:
        return _error_response(request, 503, "server_error", "sourcing budget exhausted")
    except SourcingAuthError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts rejected sourcing credentials",
        )
    except SourcingRateLimitError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts rate limit reached",
        )
    except SourcingTimeoutError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts request timed out",
        )
    except ValueError as exc:
        return _error_response(request, 422, "validation_error", str(exc))
    except SourcingClientError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts sourcing request failed",
        )

    return ok(_part_sourcing_payload(out))


def _part_sourcing_payload(
    out,
    *,
    db: Session | None = None,
    requested_currency: str | None = None,
):
    result = out.results[0]
    fx_status = _apply_fx_conversion(
        db,
        result.offers,
        requested_currency=_clean_currency(requested_currency),
    )
    return {
        "mpn": result.mpn,
        "offers": [offer.model_dump(mode="json") for offer in result.offers],
        "request_id": result.request_id,
        "tp_current_date": (
            out.tp_current_date.isoformat() if out.tp_current_date is not None else None
        ),
        "tp_response_time": out.tp_response_time,
        "powered_by": out.powered_by,
        "fetched_at": result.fetched_at.isoformat(),
        "cache_hit": result.cache_hit,
        "links": out.links.model_dump(mode="json"),
        "reason": "ok",
        "fx_status": fx_status,
    }


def _apply_fx_conversion(
    db: Session | None,
    offers,
    *,
    requested_currency: str | None,
) -> str | None:
    rates: dict[str, Decimal] | None = None
    rate_date = utcnow().date()
    fx_status: str | None = None
    fetch_error: fx_rates.FxRateError | None = None

    def fetch_today_rates() -> dict[str, Decimal]:
        nonlocal rates, fetch_error
        if db is None:
            raise fx_rates.FxRateError("database session unavailable")
        if fetch_error is not None:
            raise fetch_error
        if rates is None:
            try:
                rates = fx_rates.get_or_fetch_today(db, on_date=rate_date)
            except fx_rates.FxRateError as exc:
                fetch_error = exc
                raise
        return rates

    for offer in offers:
        for distributor in offer.distributors:
            _, status = apply_fx_to_offer(
                distributor,
                requested_currency=requested_currency,
                fetch_today_rates=fetch_today_rates,
            )
            if status == "unavailable":
                fx_status = "unavailable"

    return fx_status


def _clean_query_distributors(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    cleaned: list[str] = []
    for item in value:
        cleaned.extend(part.strip() for part in item.split(",") if part.strip())
    return cleaned or None


def _alert_payload(alert) -> dict:
    return SourcingAlertOut.model_validate(alert).model_dump(mode="json")


def _error_response(
    request: Request,
    status_code: int,
    category: str,
    message: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=err(
            category,
            message,
            request_id=getattr(request.state, "request_id", None),
        ),
    )
