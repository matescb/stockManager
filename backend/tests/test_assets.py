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


# Minimal PNG magic prefix — needed because magic-byte validation (#246)
# now rejects bodies whose leading bytes don't match the declared ext.
_PNG_BODY = b"\x89PNG\r\n\x1a\n" + b"image-bytes"


def _resp(
    status_code: int = 200,
    body: bytes | None = _PNG_BODY,
    content_type: str = "image/png",
) -> assets._AssetResponse:
    """Mimic the streaming-aware `_http_get` return value (#285).

    `body=None` simulates the streaming guard aborting because the response
    exceeded `_MAX_BYTES`; the helper sets that on `_AssetResponse.body`
    rather than handing the full buffer back to the caller.
    """
    return assets._AssetResponse(
        status_code=status_code,
        headers={"content-type": content_type},
        body=body,
    )


def test_fetch_writes_to_uploads_and_returns_local_path(monkeypatch, ws_id, tmp_path):
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    # SEC2-006 added a provider host allow-list. These pre-existing tests
    # use example.com URLs to exercise the content-addressing logic; the
    # allow-list isn't the unit under test here, so bypass it.
    monkeypatch.setattr(assets, "_host_is_allowed", lambda _h: True)
    monkeypatch.setattr(assets, "_http_get", lambda url: _resp())

    result = assets.fetch_provider_asset("https://example.com/img.png", ws_id, "image")

    assert result is not None
    assert result.startswith(f"/api/parts/assets/{ws_id}/")
    assert result.endswith(".png")

    # Filename matches the sha256 of the body.
    sha = result.rsplit("/", 1)[-1].split(".")[0]
    on_disk = tmp_path / "parts" / ws_id / f"{sha}.png"
    assert on_disk.exists()
    assert on_disk.read_bytes() == _PNG_BODY


def test_fetch_is_idempotent(monkeypatch, ws_id, tmp_path):
    """Same URL + same body → same content-hashed filename, no rewrite."""
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(assets, "_host_is_allowed", lambda _h: True)
    monkeypatch.setattr(assets, "_http_get", lambda url: _resp())

    a = assets.fetch_provider_asset("https://example.com/img.png", ws_id, "image")
    b = assets.fetch_provider_asset("https://example.com/img.png", ws_id, "image")
    assert a == b


def test_fetch_returns_none_on_404(monkeypatch, ws_id, tmp_path):
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(assets, "_host_is_allowed", lambda _h: True)
    monkeypatch.setattr(assets, "_http_get", lambda url: _resp(status_code=404, body=b""))
    assert assets.fetch_provider_asset("https://example.com/missing", ws_id, "image") is None


def test_fetch_returns_none_on_oversize(monkeypatch, ws_id, tmp_path):
    """`_http_get` signals oversize via `body=None` (#285) — the caller maps
    that to the same refusal as a 4xx / network error."""
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(assets, "_host_is_allowed", lambda _h: True)
    monkeypatch.setattr(assets, "_http_get", lambda url: _resp(body=None))
    assert assets.fetch_provider_asset("https://example.com/huge.png", ws_id, "image") is None


def test_http_get_streams_and_aborts_when_exceeds_max_bytes(monkeypatch, tmp_path):
    """`_http_get` must abort mid-stream as soon as the running byte total
    crosses `_MAX_BYTES` — never accumulating the full hostile body in
    memory (#285). We assert the iterator was abandoned before the third
    chunk by counting how many chunks the fake yielded."""
    from unittest.mock import patch

    chunks_consumed: list[int] = []

    # Three chunks: the third would push the total well past the cap.
    # The streaming loop must bail out after the second chunk and never
    # ask the iterator for the third.
    chunk_a = b"a" * (assets._MAX_BYTES // 2)
    chunk_b = b"b" * (assets._MAX_BYTES // 2 + 1)  # pushes over the cap
    chunk_c = b"c" * 1024  # must never be read

    def fake_iter_bytes(chunk_size=65536):
        for chunk in (chunk_a, chunk_b, chunk_c):
            chunks_consumed.append(len(chunk))
            yield chunk

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "image/png"}
    mock_resp.iter_bytes = fake_iter_bytes

    mock_stream = MagicMock()
    mock_stream.__enter__ = lambda s: mock_resp
    mock_stream.__exit__ = MagicMock(return_value=False)

    mock_client = MagicMock()
    mock_client.stream.return_value = mock_stream
    mock_client.__enter__ = lambda s: mock_client
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("app.domain.parts.services.assets.httpx.Client", return_value=mock_client):
        out = assets._http_get("https://media.digikey.com/huge.png")

    assert out.body is None, "oversize stream must signal refusal via body=None"
    assert out.status_code == 200
    # The fake yielded two chunks and was then abandoned — the third chunk
    # (which would have been read by a non-streaming buffer) was never
    # accumulated.
    assert chunks_consumed == [len(chunk_a), len(chunk_b)], (
        f"streaming guard read past the cap: {chunks_consumed!r}"
    )


def test_http_get_aborts_on_content_length_pre_check(monkeypatch, tmp_path):
    """Many CDNs send Content-Length; a value > `_MAX_BYTES` must short-
    circuit before any chunk is read at all (#285)."""
    from unittest.mock import patch

    chunks_read: list[bool] = []

    def fake_iter_bytes(chunk_size=65536):  # pragma: no cover - must not be called
        chunks_read.append(True)
        yield b"x"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {
        "content-type": "image/png",
        "content-length": str(assets._MAX_BYTES + 1),
    }
    mock_resp.iter_bytes = fake_iter_bytes

    mock_stream = MagicMock()
    mock_stream.__enter__ = lambda s: mock_resp
    mock_stream.__exit__ = MagicMock(return_value=False)

    mock_client = MagicMock()
    mock_client.stream.return_value = mock_stream
    mock_client.__enter__ = lambda s: mock_client
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("app.domain.parts.services.assets.httpx.Client", return_value=mock_client):
        out = assets._http_get("https://media.digikey.com/fat.png")

    assert out.body is None
    assert chunks_read == [], "Content-Length pre-check must abort before iter_bytes"


def test_http_get_ignores_malformed_content_length(monkeypatch, tmp_path):
    """A junk Content-Length (e.g. `'banana'`) must not crash — fall through
    to the chunk-counting guard."""
    from unittest.mock import patch

    body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

    def fake_iter_bytes(chunk_size=65536):
        yield body

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "image/png", "content-length": "banana"}
    mock_resp.iter_bytes = fake_iter_bytes

    mock_stream = MagicMock()
    mock_stream.__enter__ = lambda s: mock_resp
    mock_stream.__exit__ = MagicMock(return_value=False)

    mock_client = MagicMock()
    mock_client.stream.return_value = mock_stream
    mock_client.__enter__ = lambda s: mock_client
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("app.domain.parts.services.assets.httpx.Client", return_value=mock_client):
        out = assets._http_get("https://media.digikey.com/ok.png")

    assert out.body == body


def test_fetch_returns_none_on_network_error(monkeypatch, ws_id, tmp_path):
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(assets, "_host_is_allowed", lambda _h: True)
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
    monkeypatch.setattr(assets, "_host_is_allowed", lambda _h: True)
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
    monkeypatch.setattr(assets, "_host_is_allowed", lambda _h: True)
    monkeypatch.setattr(
        assets, "_http_get",
        lambda url: _resp(body=b"%PDF-1.4 fake", content_type="application/pdf"),
    )
    result = assets.fetch_provider_asset("https://example.com/foo", ws_id, "datasheet")
    assert result is not None
    assert result.endswith(".pdf")
