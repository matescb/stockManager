"""Sourcing provider endpoints."""
from __future__ import annotations

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
    except SourcingClientError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts sourcing request failed",
        )

    return ok(out.model_dump(mode="json"))


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
    except SourcingClientError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts sourcing request failed",
        )

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
):
    plan = assert_in_workspace(db, PurchasePlan, plan_id, ws.id, label="purchase plan")
    try:
        orders = sourcing_service.convert_plan_to_orders(
            db,
            workspace=ws,
            plan=plan,
            user_id=user.id,
        )
    except sourcing_service.PurchasePlanStaleError as exc:
        return _error_response(request, 409, "conflict", str(exc))
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
    except SourcingClientError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts sourcing request failed",
        )

    result = out.results[0]
    return ok(
        {
            "mpn": result.mpn,
            "offers": [offer.model_dump(mode="json") for offer in result.offers],
            "request_id": result.request_id,
            "powered_by": out.powered_by,
            "fetched_at": result.fetched_at.isoformat(),
            "cache_hit": result.cache_hit,
            "links": out.links.model_dump(mode="json"),
            "reason": "ok",
        }
    )


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
    except SourcingClientError:
        return _error_response(
            request,
            502,
            "server_error",
            "TrustedParts sourcing request failed",
        )

    return ok(_part_sourcing_payload(out))


def _part_sourcing_payload(out):
    result = out.results[0]
    return {
        "mpn": result.mpn,
        "offers": [offer.model_dump(mode="json") for offer in result.offers],
        "request_id": result.request_id,
        "powered_by": out.powered_by,
        "fetched_at": result.fetched_at.isoformat(),
        "cache_hit": result.cache_hit,
        "links": out.links.model_dump(mode="json"),
        "reason": "ok",
    }


def _clean_query_distributors(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    cleaned: list[str] = []
    for item in value:
        cleaned.extend(part.strip() for part in item.split(",") if part.strip())
    return cleaned or None


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
