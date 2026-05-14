from __future__ import annotations

import httpx

from app.domain.sourcing.providers import make_retrying_client


def test_retrying_client_splits_connect_read_write_pool_timeouts() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, request=request))

    with make_retrying_client(
        provider_name="trustedparts",
        timeout=8.0,
        transport=transport,
    ) as client:
        assert client.timeout.connect == 2.0
        assert client.timeout.read == 8.0
        assert client.timeout.write == 8.0
        assert client.timeout.pool == 2.0
