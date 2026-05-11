from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from app.domain.sourcing.providers._retry_transport import RetryingAsyncHTTPTransport


async def _request_with_transport(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    sleeps: list[float],
    monkeypatch: pytest.MonkeyPatch,
    max_retries: int = 3,
) -> tuple[httpx.Response, int]:
    attempts = 0

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    def wrapped(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return handler(request)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    transport = RetryingAsyncHTTPTransport(
        provider_name="trustedparts",
        inner=httpx.MockTransport(wrapped),
        base_delay=0.5,
        max_delay=8.0,
        max_retries=max_retries,
        verify=True,
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get("https://api.trustedparts.com/v2/search")
    return response, attempts


def test_retries_on_429_then_succeeds(monkeypatch, caplog):
    statuses = [429, 429, 200]
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(statuses.pop(0), request=request)

    caplog.set_level(logging.INFO)
    response, attempts = asyncio.run(
        _request_with_transport(handler, sleeps=sleeps, monkeypatch=monkeypatch)
    )

    assert response.status_code == 200
    assert attempts == 3
    assert len(sleeps) == 2
    assert "provider_retry provider=trustedparts status=429 attempt=1" in caplog.text


def test_retries_on_503_then_succeeds(monkeypatch):
    statuses = [503, 200]
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(statuses.pop(0), request=request)

    response, attempts = asyncio.run(
        _request_with_transport(handler, sleeps=sleeps, monkeypatch=monkeypatch)
    )

    assert response.status_code == 200
    assert attempts == 2
    assert len(sleeps) == 1


def test_does_not_retry_on_400(monkeypatch):
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, request=request)

    response, attempts = asyncio.run(
        _request_with_transport(handler, sleeps=sleeps, monkeypatch=monkeypatch)
    )

    assert response.status_code == 400
    assert attempts == 1
    assert sleeps == []


def test_exhausts_retries_and_returns_last_response(monkeypatch, caplog):
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, request=request)

    caplog.set_level(logging.INFO)
    response, attempts = asyncio.run(
        _request_with_transport(handler, sleeps=sleeps, monkeypatch=monkeypatch)
    )

    assert response.status_code == 429
    assert attempts == 4
    assert len(sleeps) == 3
    assert "provider_retry_exhausted provider=trustedparts status=429 attempts=4" in caplog.text


def test_retries_on_connect_error(monkeypatch):
    sleeps: list[float] = []
    seen = {"raised": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if not seen["raised"]:
            seen["raised"] = True
            raise httpx.ConnectError("simulated connect failure", request=request)
        return httpx.Response(200, request=request)

    response, attempts = asyncio.run(
        _request_with_transport(handler, sleeps=sleeps, monkeypatch=monkeypatch)
    )

    assert response.status_code == 200
    assert attempts == 2
    assert len(sleeps) == 1


def test_honors_retry_after_header(monkeypatch):
    sleeps: list[float] = []
    statuses = [429, 200]

    def handler(request: httpx.Request) -> httpx.Response:
        status = statuses.pop(0)
        headers = {"Retry-After": "2"} if status == 429 else {}
        return httpx.Response(status, headers=headers, request=request)

    response, attempts = asyncio.run(
        _request_with_transport(handler, sleeps=sleeps, monkeypatch=monkeypatch)
    )

    assert response.status_code == 200
    assert attempts == 2
    assert sleeps == [2.0]


def test_honors_retry_after_http_date_capped(monkeypatch):
    sleeps: list[float] = []
    retry_at = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=60))
    statuses = [503, 200]

    def handler(request: httpx.Request) -> httpx.Response:
        status = statuses.pop(0)
        headers = {"Retry-After": retry_at} if status == 503 else {}
        return httpx.Response(status, headers=headers, request=request)

    response, attempts = asyncio.run(
        _request_with_transport(handler, sleeps=sleeps, monkeypatch=monkeypatch)
    )

    assert response.status_code == 200
    assert attempts == 2
    assert sleeps == [8.0]


@pytest.mark.parametrize("status", [401, 403, 404, 422, 500])
def test_no_retry_for_non_retryable_status(monkeypatch, status):
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, request=request)

    response, attempts = asyncio.run(
        _request_with_transport(handler, sleeps=sleeps, monkeypatch=monkeypatch)
    )

    assert response.status_code == status
    assert attempts == 1
    assert sleeps == []
