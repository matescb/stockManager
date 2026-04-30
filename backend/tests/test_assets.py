"""Provider-asset download helper.

The helper is content-addressed + fail-tolerant: anything that prevents
a successful download (404, oversize, network error, non-http URL) maps
to None so the caller falls back to the remote URL. These tests pin the
contract by mocking the single _http_get seam.
"""
from __future__ import annotations

import os
import uuid
from unittest.mock import MagicMock

import pytest

from app.core.config import settings
from app.domain.parts.services import assets


@pytest.fixture
def ws_id() -> str:
    return str(uuid.uuid4())


def _resp(status_code: int = 200, body: bytes = b"image-bytes", content_type: str = "image/png") -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.content = body
    r.headers = {"content-type": content_type}
    return r


def test_fetch_writes_to_uploads_and_returns_local_path(monkeypatch, ws_id, tmp_path):
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(assets, "_http_get", lambda url: _resp())

    result = assets.fetch_provider_asset("https://example.com/img.png", ws_id, "image")

    assert result is not None
    assert result.startswith(f"/api/parts/assets/{ws_id}/")
    assert result.endswith(".png")

    # Filename matches the sha256 of the body.
    sha = result.rsplit("/", 1)[-1].split(".")[0]
    on_disk = tmp_path / "parts" / ws_id / f"{sha}.png"
    assert on_disk.exists()
    assert on_disk.read_bytes() == b"image-bytes"


def test_fetch_is_idempotent(monkeypatch, ws_id, tmp_path):
    """Same URL + same body → same content-hashed filename, no rewrite."""
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(assets, "_http_get", lambda url: _resp())

    a = assets.fetch_provider_asset("https://example.com/img.png", ws_id, "image")
    b = assets.fetch_provider_asset("https://example.com/img.png", ws_id, "image")
    assert a == b


def test_fetch_returns_none_on_404(monkeypatch, ws_id, tmp_path):
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(assets, "_http_get", lambda url: _resp(status_code=404, body=b""))
    assert assets.fetch_provider_asset("https://example.com/missing", ws_id, "image") is None


def test_fetch_returns_none_on_oversize(monkeypatch, ws_id, tmp_path):
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    huge = b"x" * (assets._MAX_BYTES + 1)
    monkeypatch.setattr(assets, "_http_get", lambda url: _resp(body=huge))
    assert assets.fetch_provider_asset("https://example.com/huge.png", ws_id, "image") is None


def test_fetch_returns_none_on_network_error(monkeypatch, ws_id, tmp_path):
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    def boom(_url):
        raise RuntimeError("simulated DNS failure")
    monkeypatch.setattr(assets, "_http_get", boom)
    assert assets.fetch_provider_asset("https://example.com/img.png", ws_id, "image") is None


def test_fetch_skips_non_http_urls(ws_id):
    assert assets.fetch_provider_asset("", ws_id, "image") is None
    assert assets.fetch_provider_asset("file:///etc/passwd", ws_id, "image") is None
    assert assets.fetch_provider_asset("javascript:alert(1)", ws_id, "image") is None


def test_fetch_falls_back_to_url_extension(monkeypatch, ws_id, tmp_path):
    """Content-Type is sometimes generic (application/octet-stream).
    Fall back to the URL's path suffix so the file lands with a usable extension."""
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(
        assets, "_http_get",
        lambda url: _resp(body=b"%PDF-1.4 fake", content_type="application/octet-stream"),
    )
    result = assets.fetch_provider_asset(
        "https://example.com/datasheets/SOMETHING.pdf?download=1", ws_id, "datasheet"
    )
    assert result is not None
    assert result.endswith(".pdf")


def test_fetch_pdf_content_type_yields_pdf(monkeypatch, ws_id, tmp_path):
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(
        assets, "_http_get",
        lambda url: _resp(body=b"%PDF-1.4 fake", content_type="application/pdf"),
    )
    result = assets.fetch_provider_asset("https://example.com/foo", ws_id, "datasheet")
    assert result is not None
    assert result.endswith(".pdf")
