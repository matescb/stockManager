from __future__ import annotations

from collections.abc import AsyncIterator

import anyio
import pytest
from fastapi import HTTPException, status

from app.api.routes import sentry_tunnel as sentry_tunnel_route
from app.core.errors import ErrorCodes


class _ChunkedRequest:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def stream(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


def test_chunk_count_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sentry_tunnel_route, "SENTRY_TUNNEL_MAX_CHUNKS", 2)
    request = _ChunkedRequest([b"a", b"b", b"c"])

    async def read() -> bytes:
        return await sentry_tunnel_route._read_bounded_envelope(request, 1024)

    with pytest.raises(HTTPException) as exc:
        anyio.run(read)

    assert exc.value.status_code == status.HTTP_413_CONTENT_TOO_LARGE
    assert exc.value.detail["code"] == ErrorCodes.SENTRY_TUNNEL_TOO_LARGE
    assert exc.value.detail["max_chunks"] == 2
