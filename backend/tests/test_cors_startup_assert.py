from __future__ import annotations

import pytest
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.main import CORS_ALLOW_HEADERS, app, lifespan


@pytest.mark.asyncio
async def test_wildcard_in_prod_rejected(monkeypatch):
    cfg = settings()
    monkeypatch.setattr(cfg, "APP_ENV", "prod")
    monkeypatch.setattr(cfg, "CORS_ORIGINS", "https://parts.matescb.cz, *")

    with pytest.raises(RuntimeError, match=r"CORS_ORIGINS=\*"):
        async with lifespan(app):
            pass


def test_cors_allow_headers_are_explicit():
    middleware = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    allowed_headers = middleware.kwargs["allow_headers"]

    assert allowed_headers == CORS_ALLOW_HEADERS
    assert "*" not in allowed_headers
