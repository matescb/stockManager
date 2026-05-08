"""Sourcing provider endpoints."""
from __future__ import annotations

from time import perf_counter

from fastapi import APIRouter, Depends, Request

from app.core.deps import CurrentWorkspace, require_role
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import ok
from app.core.secrets import decrypt
from app.domain.sourcing import (
    SourcingAuthError,
    SourcingClientError,
    SourcingRateLimitError,
    SourcingTimeoutError,
    TrustedPartsClient,
)
from app.domain.sourcing.schemas import SourcingQuery

router = APIRouter()

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
    if (
        ws.sourcing_provider != "trustedparts"
        or not ws.sourcing_company_id_enc
        or not ws.sourcing_api_key_enc
    ):
        return _test_result(False, "not configured", 0)

    company_id = decrypt(ws.sourcing_company_id_enc)
    api_key = decrypt(ws.sourcing_api_key_enc)
    if not company_id or not api_key:
        return _test_result(False, "not configured", 0)

    # TODO(#326): replace "dev" with the repository git SHA once a shared
    # app.core helper exposes it.
    user_agent = f"stockManager/dev workspace={ws.id}"
    client = TrustedPartsClient(
        company_id=company_id,
        api_key=api_key,
        country_code=ws.sourcing_country_code,
        currency_code=ws.sourcing_currency_code,
        user_agent=user_agent,
    )

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
