"""Sourcing provider endpoints."""
from __future__ import annotations

from time import perf_counter

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, CurrentWorkspace, require_role
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import err, ok
from app.domain.sourcing import (
    SourcingAuthError,
    SourcingClientError,
    SourcingRateLimitError,
    SourcingTimeoutError,
)
from app.domain.sourcing import service as sourcing_service
from app.domain.sourcing.factory import make_sourcing_provider
from app.domain.sourcing.schemas import SourcingQuery, SourcingSearchIn
from app.domain.sourcing.service import SourcingBudgetBlocked, SourcingNotConfigured
from app.infra.db import get_db

router = APIRouter()
search_router = APIRouter()

_TEST_PROBE_TOKEN = "TEST_PROBE_DO_NOT_BUY"


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
