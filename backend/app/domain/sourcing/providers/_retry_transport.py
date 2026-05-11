"""Bounded retry transports for outbound provider HTTP calls."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

RETRYABLE_STATUSES = frozenset({429, 503})
RETRYABLE_EXCEPTIONS = (httpx.ConnectError, httpx.ReadTimeout)

logger = logging.getLogger(__name__)


class RetryingHTTPTransport(httpx.HTTPTransport):
    """Bounded exponential backoff on retryable upstream responses/errors."""

    def __init__(
        self,
        *args,
        max_retries: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 8.0,
        retryable_statuses: Iterable[int] = RETRYABLE_STATUSES,
        provider_name: str | None = None,
        inner: httpx.BaseTransport | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._max_retries = max_retries
        self._base = base_delay
        self._cap = max_delay
        self._retryable = frozenset(retryable_statuses)
        self._provider_name = provider_name
        self._inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response: httpx.Response | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._handle_once(request)
            except RETRYABLE_EXCEPTIONS as exc:
                if attempt == self._max_retries:
                    _log_exhausted(request, self._provider_name, attempt, exc=exc)
                    raise
                delay = self._compute_delay(attempt)
                _log_retry(request, self._provider_name, attempt, delay, exc=exc)
                time.sleep(delay)
                continue

            if response.status_code not in self._retryable:
                return response
            if attempt == self._max_retries:
                _log_exhausted(request, self._provider_name, attempt, response=response)
                return response

            delay = _retry_after_seconds(response, self._cap)
            if delay is None:
                delay = self._compute_delay(attempt)
            _log_retry(request, self._provider_name, attempt, delay, response=response)
            response.close()
            time.sleep(delay)

        if response is None:  # pragma: no cover - defensive loop guard.
            raise RuntimeError("retry transport exited without response")
        return response

    def close(self) -> None:
        if self._inner is not None:
            self._inner.close()
        super().close()

    def _handle_once(self, request: httpx.Request) -> httpx.Response:
        if self._inner is not None:
            return self._inner.handle_request(request)
        return super().handle_request(request)

    def _compute_delay(self, attempt: int) -> float:
        exp = min(self._base * (2**attempt), self._cap)
        return random.uniform(0, exp)


class RetryingAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """Async variant for future provider clients using httpx.AsyncClient."""

    def __init__(
        self,
        *args,
        max_retries: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 8.0,
        retryable_statuses: Iterable[int] = RETRYABLE_STATUSES,
        provider_name: str | None = None,
        inner: httpx.AsyncBaseTransport | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._max_retries = max_retries
        self._base = base_delay
        self._cap = max_delay
        self._retryable = frozenset(retryable_statuses)
        self._provider_name = provider_name
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response: httpx.Response | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._handle_once(request)
            except RETRYABLE_EXCEPTIONS as exc:
                if attempt == self._max_retries:
                    _log_exhausted(request, self._provider_name, attempt, exc=exc)
                    raise
                delay = self._compute_delay(attempt)
                _log_retry(request, self._provider_name, attempt, delay, exc=exc)
                await asyncio.sleep(delay)
                continue

            if response.status_code not in self._retryable:
                return response
            if attempt == self._max_retries:
                _log_exhausted(request, self._provider_name, attempt, response=response)
                return response

            delay = _retry_after_seconds(response, self._cap)
            if delay is None:
                delay = self._compute_delay(attempt)
            _log_retry(request, self._provider_name, attempt, delay, response=response)
            await response.aclose()
            await asyncio.sleep(delay)

        if response is None:  # pragma: no cover - defensive loop guard.
            raise RuntimeError("retry transport exited without response")
        return response

    async def aclose(self) -> None:
        if self._inner is not None:
            await self._inner.aclose()
        await super().aclose()

    async def _handle_once(self, request: httpx.Request) -> httpx.Response:
        if self._inner is not None:
            return await self._inner.handle_async_request(request)
        return await super().handle_async_request(request)

    def _compute_delay(self, attempt: int) -> float:
        exp = min(self._base * (2**attempt), self._cap)
        return random.uniform(0, exp)


def _retry_after_seconds(response: httpx.Response, max_delay: float) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        seconds = _retry_after_http_date_seconds(value)
        if seconds is None:
            return None
    return min(max(0.0, seconds), max_delay)


def _retry_after_http_date_seconds(value: str) -> float | None:
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (when - datetime.now(timezone.utc)).total_seconds()


def _log_retry(
    request: httpx.Request,
    provider_name: str | None,
    attempt: int,
    delay: float,
    *,
    response: httpx.Response | None = None,
    exc: Exception | None = None,
) -> None:
    logger.info(
        "provider_retry provider=%s status=%s attempt=%s delay=%.3f url=%s error=%s",
        provider_name or _provider_from_url(request.url),
        response.status_code if response is not None else None,
        attempt + 1,
        delay,
        request.url.host,
        type(exc).__name__ if exc is not None else None,
    )


def _log_exhausted(
    request: httpx.Request,
    provider_name: str | None,
    attempt: int,
    *,
    response: httpx.Response | None = None,
    exc: Exception | None = None,
) -> None:
    logger.warning(
        "provider_retry_exhausted provider=%s status=%s attempts=%s url=%s error=%s",
        provider_name or _provider_from_url(request.url),
        response.status_code if response is not None else None,
        attempt + 1,
        request.url.host,
        type(exc).__name__ if exc is not None else None,
    )


def _provider_from_url(url: httpx.URL) -> str:
    host = (url.host or "").lower()
    if "trustedparts" in host:
        return "trustedparts"
    if "mouser" in host:
        return "mouser"
    if "digikey" in host:
        return "digikey"
    return host or "unknown"
