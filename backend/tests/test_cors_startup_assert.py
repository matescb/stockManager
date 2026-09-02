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


@pytest.mark.asyncio
async def test_empty_cors_origins_in_prod_rejected(monkeypatch):
    cfg = settings()
    monkeypatch.setattr(cfg, "APP_ENV", "prod")
    monkeypatch.setattr(cfg, "CORS_ORIGINS", "")

    with pytest.raises(RuntimeError, match="requires at least one CORS_ORIGINS"):
        async with lifespan(app):
            pass


def test_cors_allow_headers_are_explicit():
    middleware = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    allowed_headers = middleware.kwargs["allow_headers"]

    assert allowed_headers == CORS_ALLOW_HEADERS
    assert "*" not in allowed_headers


def test_authorization_is_not_a_cors_allowed_header():
    """Load-bearing for the CSRF exemption (ADR-0029).

    `CsrfOriginMiddleware` skips the Origin check whenever an
    `Authorization` header is present. One of the two legs holding that
    up is that a browser cannot attach that header cross-site without a
    CORS preflight — and the preflight fails because we never allow the
    header. Adding "Authorization" here would let an allow-listed origin
    preflight it successfully and weaken the argument.
    """
    assert "authorization" not in {h.lower() for h in CORS_ALLOW_HEADERS}
