from __future__ import annotations

import pytest

from app.core.config import settings
from app.main import assert_proxy_headers_trusted


def test_assert_proxy_headers_enabled(monkeypatch):
    cfg = settings()
    monkeypatch.setattr(cfg, "APP_ENV", "prod")

    with pytest.raises(RuntimeError, match="--proxy-headers"):
        assert_proxy_headers_trusted(["uvicorn", "app.main:app"])


def test_assert_proxy_headers_requires_trusted_forwarded_ips(monkeypatch):
    cfg = settings()
    monkeypatch.setattr(cfg, "APP_ENV", "prod")

    with pytest.raises(RuntimeError, match=r"--forwarded-allow-ips=\*"):
        assert_proxy_headers_trusted(["uvicorn", "app.main:app", "--proxy-headers"])


def test_assert_proxy_headers_rejects_explicit_disable(monkeypatch):
    cfg = settings()
    monkeypatch.setattr(cfg, "APP_ENV", "prod")

    with pytest.raises(RuntimeError, match="--proxy-headers"):
        assert_proxy_headers_trusted([
            "uvicorn",
            "app.main:app",
            "--proxy-headers",
            "--no-proxy-headers",
            "--forwarded-allow-ips=*",
        ])


def test_assert_proxy_headers_accepts_prod_compose_argv(monkeypatch):
    cfg = settings()
    monkeypatch.setattr(cfg, "APP_ENV", "prod")

    assert_proxy_headers_trusted([
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--workers",
        "1",
        "--proxy-headers",
        "--forwarded-allow-ips=*",
        "--timeout-graceful-shutdown",
        "25",
    ])
