"""HTTP client factory for outbound provider calls."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

import httpx

from app.domain.sourcing.providers._retry_transport import RetryingHTTPTransport

CONNECT_TIMEOUT_SECONDS = 2.0
POOL_TIMEOUT_SECONDS = 2.0
KEEPALIVE_EXPIRY_SECONDS = 30.0
MAX_CONNECTIONS = 20
MAX_KEEPALIVE_CONNECTIONS = 10

_CLIENTS: dict[str, httpx.Client] = {}
_CLIENTS_LOCK = Lock()


def _timeout(timeout: float) -> httpx.Timeout:
    return httpx.Timeout(
        connect=CONNECT_TIMEOUT_SECONDS,
        read=timeout,
        write=timeout,
        pool=POOL_TIMEOUT_SECONDS,
    )


def _retry_transport(
    *,
    provider_name: str,
    transport: httpx.BaseTransport | None = None,
    limits: httpx.Limits | None = None,
) -> RetryingHTTPTransport:
    kwargs = {"limits": limits} if limits is not None else {}
    return RetryingHTTPTransport(
        verify=True,
        provider_name=provider_name,
        inner=transport,
        **kwargs,
    )


def _limits() -> httpx.Limits:
    return httpx.Limits(
        max_connections=MAX_CONNECTIONS,
        max_keepalive_connections=MAX_KEEPALIVE_CONNECTIONS,
        keepalive_expiry=KEEPALIVE_EXPIRY_SECONDS,
    )


def _pooled_client(*, provider_name: str, timeout: float) -> httpx.Client:
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(provider_name)
        if client is None or client.is_closed:
            client = httpx.Client(
                timeout=_timeout(timeout),
                transport=_retry_transport(
                    provider_name=provider_name,
                    limits=_limits(),
                ),
            )
            _CLIENTS[provider_name] = client
        return client


@contextmanager
def make_retrying_client(
    *,
    provider_name: str,
    timeout: float,
    transport: httpx.BaseTransport | None = None,
) -> Iterator[httpx.Client]:
    """Return a provider HTTP client with the shared retry policy."""
    if transport is not None:
        with httpx.Client(
            timeout=_timeout(timeout),
            transport=_retry_transport(
                provider_name=provider_name,
                transport=transport,
            ),
        ) as client:
            yield client
        return

    yield _pooled_client(provider_name=provider_name, timeout=timeout)


def close_provider_client_pool() -> None:
    """Close all pooled provider HTTP clients.

    FastAPI calls this during lifespan shutdown so graceful process exits
    release keep-alive sockets promptly instead of waiting for interpreter
    teardown.
    """
    with _CLIENTS_LOCK:
        clients = list(_CLIENTS.values())
        _CLIENTS.clear()
    for client in clients:
        client.close()


def _reset_provider_client_pool_for_tests() -> None:
    close_provider_client_pool()
