"""HTTP client factory for outbound provider calls."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import httpx

from app.domain.sourcing.providers._retry_transport import RetryingHTTPTransport


@contextmanager
def make_retrying_client(
    *,
    provider_name: str,
    timeout: float,
    transport: httpx.BaseTransport | None = None,
) -> Iterator[httpx.Client]:
    """Build a provider HTTP client with the shared retry policy."""
    retry_transport = RetryingHTTPTransport(
        verify=True,
        provider_name=provider_name,
        inner=transport,
    )
    with httpx.Client(timeout=timeout, transport=retry_transport) as client:
        yield client
