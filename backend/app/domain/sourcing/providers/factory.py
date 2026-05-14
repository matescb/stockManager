"""HTTP client factory for outbound provider calls."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import httpx

from app.domain.sourcing.providers._retry_transport import RetryingHTTPTransport

CONNECT_TIMEOUT_SECONDS = 2.0
POOL_TIMEOUT_SECONDS = 2.0


@contextmanager
def make_retrying_client(
    *,
    provider_name: str,
    timeout: float,
    transport: httpx.BaseTransport | None = None,
) -> Iterator[httpx.Client]:
    """Build a provider HTTP client with the shared retry policy."""
    split_timeout = httpx.Timeout(
        connect=CONNECT_TIMEOUT_SECONDS,
        read=timeout,
        write=timeout,
        pool=POOL_TIMEOUT_SECONDS,
    )
    retry_transport = RetryingHTTPTransport(
        verify=True,
        provider_name=provider_name,
        inner=transport,
    )
    with httpx.Client(timeout=split_timeout, transport=retry_transport) as client:
        yield client
