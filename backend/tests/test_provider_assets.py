"""Regression tests for SEC2-006 / SEC2-011 / SEC2-012 (provider-asset hardening).

Covers:
- Host allow-list — only Mouser / DigiKey hosts pass.
- DNS resolution to a non-public IP is rejected (SSRF guard).
- Upstream SVG content-type is rejected (lands as `.bin`).
- 30x upstream is treated as a refusal — no auto-redirect.
- Magic-byte validation: body whose leading bytes don't match the
  Content-Type-declared extension is rejected (SEC2-012).
- The serve route forces `X-Content-Type-Options: nosniff` on every
  response and forces `Content-Disposition: attachment` for non-image
  MIMEs (PDF datasheets, opaque binaries).
"""
from __future__ import annotations

import hashlib
import os
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.domain.parts.services import assets
from app.main import app


def _resp(status_code: int = 200, body: bytes = b"image-bytes", content_type: str = "image/png") -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.content = body
    r.headers = {"content-type": content_type}
    return r


# ---------------------------------------------------------------------------
# fetch_provider_asset — host allow-list + SSRF guard + redirect refusal
# ---------------------------------------------------------------------------


def test_rejects_host_not_on_allow_list(monkeypatch, tmp_path):
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    called = {"hit": False}

    def _spy(_url):  # pragma: no cover - should never be called
        called["hit"] = True
        return _resp()

    monkeypatch.setattr(assets, "_http_get", _spy)
    out = assets.fetch_provider_asset("https://evil.example.com/x.png", str(uuid.uuid4()), "image")
    assert out is None
    assert called["hit"] is False, "must short-circuit before issuing the HTTP GET"


def test_rejects_host_resolving_to_private_ip(monkeypatch, tmp_path):
    """Even an allow-listed host gets refused if DNS hands back a non-public
    IP (defence against DNS rebinding / homoglyph confusion / lab DNS)."""
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(assets.socket, "gethostbyname", lambda _h: "10.0.0.5")
    monkeypatch.setattr(assets, "_http_get", lambda _u: _resp())
    out = assets.fetch_provider_asset(
        "https://www.mouser.com/foo.png", str(uuid.uuid4()), "image"
    )
    assert out is None


def test_rejects_loopback_resolution(monkeypatch, tmp_path):
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(assets.socket, "gethostbyname", lambda _h: "127.0.0.1")
    monkeypatch.setattr(assets, "_http_get", lambda _u: _resp())
    assert assets.fetch_provider_asset(
        "https://media.digikey.com/foo.png", str(uuid.uuid4()), "image"
    ) is None


def test_rejects_aws_metadata_resolution(monkeypatch, tmp_path):
    """169.254/16 is link-local; ip.is_global is False so it must be refused."""
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(assets.socket, "gethostbyname", lambda _h: "169.254.169.254")
    monkeypatch.setattr(assets, "_http_get", lambda _u: _resp())
    assert assets.fetch_provider_asset(
        "https://www.mouser.com/foo.png", str(uuid.uuid4()), "image"
    ) is None


def test_allowed_host_with_public_ip_succeeds(monkeypatch, tmp_path):
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(assets.socket, "gethostbyname", lambda _h: "93.184.216.34")
    monkeypatch.setattr(assets, "_http_get", lambda _u: _resp())
    out = assets.fetch_provider_asset(
        "https://media.digikey.com/parts/img.png", str(uuid.uuid4()), "image"
    )
    assert out is not None
    assert out.endswith(".png")


def test_redirect_response_is_refused(monkeypatch, tmp_path):
    """The helper sets follow_redirects=False; a 30x is treated as a refusal."""
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(assets.socket, "gethostbyname", lambda _h: "93.184.216.34")
    monkeypatch.setattr(
        assets, "_http_get",
        lambda _u: _resp(status_code=302, body=b"", content_type="text/html"),
    )
    assert assets.fetch_provider_asset(
        "https://www.mouser.com/foo.png", str(uuid.uuid4()), "image"
    ) is None


def test_svg_content_type_lands_as_bin(monkeypatch, tmp_path):
    """SEC2-006 — SVG can carry inline JS; refuse to map it to .svg.
    The body still lands on disk because the helper is best-effort, but
    with a `.bin` extension which the serve route forces to download."""
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(assets.socket, "gethostbyname", lambda _h: "93.184.216.34")
    body = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    monkeypatch.setattr(
        assets, "_http_get",
        lambda _u: _resp(body=body, content_type="image/svg+xml"),
    )
    out = assets.fetch_provider_asset(
        "https://media.digikey.com/foo.svg", str(uuid.uuid4()), "image"
    )
    assert out is not None
    assert out.endswith(".bin"), f"expected .bin, got {out}"


def test_svg_url_suffix_lands_as_bin(monkeypatch, tmp_path):
    """An upstream that serves SVG but advertises octet-stream still ends
    up with .bin — the URL-suffix fallback also blocks .svg."""
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(assets.socket, "gethostbyname", lambda _h: "93.184.216.34")
    monkeypatch.setattr(
        assets, "_http_get",
        lambda _u: _resp(body=b"<svg/>", content_type="application/octet-stream"),
    )
    out = assets.fetch_provider_asset(
        "https://media.digikey.com/foo.svg", str(uuid.uuid4()), "image"
    )
    assert out is not None
    assert out.endswith(".bin")


# ---------------------------------------------------------------------------
# Magic-byte validation (SEC2-012)
# ---------------------------------------------------------------------------


def test_magic_bytes_mismatch_png_declared_pdf_rejected(monkeypatch, tmp_path):
    """Body starts with PDF magic but Content-Type says image/png → rejected."""
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(assets.socket, "gethostbyname", lambda _h: "93.184.216.34")
    monkeypatch.setattr(
        assets, "_http_get",
        lambda _u: _resp(body=b"%PDF-1.4 this is not a PNG", content_type="image/png"),
    )
    out = assets.fetch_provider_asset(
        "https://media.digikey.com/fake.png", str(uuid.uuid4()), "image"
    )
    assert out is None, "PDF magic masquerading as PNG must be rejected"


def test_magic_bytes_mismatch_jpg_declared_pdf_rejected(monkeypatch, tmp_path):
    """Body starts with JPEG magic but Content-Type says application/pdf → rejected."""
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(assets.socket, "gethostbyname", lambda _h: "93.184.216.34")
    jpeg_magic = b"\xff\xd8\xff\xe0" + b"\x00" * 12
    monkeypatch.setattr(
        assets, "_http_get",
        lambda _u: _resp(body=jpeg_magic, content_type="application/pdf"),
    )
    out = assets.fetch_provider_asset(
        "https://media.digikey.com/fake.pdf", str(uuid.uuid4()), "datasheet"
    )
    assert out is None, "JPEG magic masquerading as PDF must be rejected"


def test_magic_bytes_match_png_accepted(monkeypatch, tmp_path):
    """Body starts with PNG magic and Content-Type says image/png → accepted."""
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(assets.socket, "gethostbyname", lambda _h: "93.184.216.34")
    png_body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    monkeypatch.setattr(
        assets, "_http_get",
        lambda _u: _resp(body=png_body, content_type="image/png"),
    )
    out = assets.fetch_provider_asset(
        "https://media.digikey.com/real.png", str(uuid.uuid4()), "image"
    )
    assert out is not None
    assert out.endswith(".png")


def test_magic_bytes_match_pdf_accepted(monkeypatch, tmp_path):
    """Body starts with PDF magic and Content-Type says application/pdf → accepted."""
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(assets.socket, "gethostbyname", lambda _h: "93.184.216.34")
    pdf_body = b"%PDF-1.4 fake content"
    monkeypatch.setattr(
        assets, "_http_get",
        lambda _u: _resp(body=pdf_body, content_type="application/pdf"),
    )
    out = assets.fetch_provider_asset(
        "https://media.digikey.com/real.pdf", str(uuid.uuid4()), "datasheet"
    )
    assert out is not None
    assert out.endswith(".pdf")


def test_magic_bytes_unknown_body_with_known_ext_accepted(monkeypatch, tmp_path):
    """If _sniff_ext returns None (unknown magic bytes), we don't block —
    the file type just can't be sniffed so we trust the Content-Type."""
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(assets.socket, "gethostbyname", lambda _h: "93.184.216.34")
    # Body with no recognisable magic prefix.
    monkeypatch.setattr(
        assets, "_http_get",
        lambda _u: _resp(body=b"\x00\x00\x00\x00unknown format bytes", content_type="image/png"),
    )
    out = assets.fetch_provider_asset(
        "https://media.digikey.com/opaque.png", str(uuid.uuid4()), "image"
    )
    # sniff is None → no rejection, falls through.
    assert out is not None
    assert out.endswith(".png")


def test_magic_bytes_gif_mismatch_rejected(monkeypatch, tmp_path):
    """PNG body disguised as GIF (Content-Type image/gif) is rejected."""
    monkeypatch.setattr(settings(), "UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(assets.socket, "gethostbyname", lambda _h: "93.184.216.34")
    png_body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    monkeypatch.setattr(
        assets, "_http_get",
        lambda _u: _resp(body=png_body, content_type="image/gif"),
    )
    out = assets.fetch_provider_asset(
        "https://media.digikey.com/fake.gif", str(uuid.uuid4()), "image"
    )
    assert out is None, "PNG magic masquerading as GIF must be rejected"


def test_sniff_ext_returns_correct_types():
    """Unit test for the _sniff_ext helper in isolation."""
    assert assets._sniff_ext(b"\xff\xd8\xff\xe0" + b"\x00" * 12) == "jpg"
    assert assets._sniff_ext(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8) == "png"
    assert assets._sniff_ext(b"%PDF-1.4 content") == "pdf"
    assert assets._sniff_ext(b"GIF89a content") == "gif"
    assert assets._sniff_ext(b"RIFF\x00\x00\x00\x00WEBP") == "webp"
    # Unknown magic → None
    assert assets._sniff_ext(b"\x00\x00\x00\x00junk") is None
    # RIFF without WEBP marker → None (not a WebP)
    assert assets._sniff_ext(b"RIFF\x00\x00\x00\x00WAVEfmt ") is None


# ---------------------------------------------------------------------------
# Serve route — nosniff + content-disposition for non-images
# ---------------------------------------------------------------------------


def _signup(c: TestClient) -> str:
    email = f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post("/api/auth/signup", json={"email": email, "name": "u", "password": "TestPass-2026-Stronk"})
    assert r.status_code == 200, r.text
    return r.json()["data"]["workspace_id"]


def _seed_asset(ws_id: str, body: bytes, ext: str) -> str:
    sha = hashlib.sha256(body).hexdigest()
    target_dir = os.path.join(settings().UPLOAD_DIR, "parts", ws_id)
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, f"{sha}.{ext}")
    with open(path, "wb") as f:
        f.write(body)
    return f"{sha}.{ext}"


def test_served_image_carries_nosniff_and_inline_disposition():
    c = TestClient(app)
    ws_id = _signup(c)
    fname = _seed_asset(ws_id, b"\x89PNG\r\n\x1a\nfake", "png")
    r = c.get(f"/api/parts/assets/{ws_id}/{fname}")
    assert r.status_code == 200, r.text
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-type"].startswith("image/png")
    # No `name=` query param → no Content-Disposition (image is inline).
    assert "attachment" not in r.headers.get("content-disposition", "").lower()


def test_served_pdf_forces_attachment_with_nosniff():
    c = TestClient(app)
    ws_id = _signup(c)
    fname = _seed_asset(ws_id, b"%PDF-1.4 fake", "pdf")
    r = c.get(f"/api/parts/assets/{ws_id}/{fname}")
    assert r.status_code == 200, r.text
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-type"].startswith("application/pdf")
    assert "attachment" in r.headers.get("content-disposition", "").lower()


def test_served_pdf_with_name_param_uses_attachment_filename():
    c = TestClient(app)
    ws_id = _signup(c)
    fname = _seed_asset(ws_id, b"%PDF-1.4 fake", "pdf")
    r = c.get(f"/api/parts/assets/{ws_id}/{fname}?name=mydatasheet")
    assert r.status_code == 200, r.text
    cd = r.headers.get("content-disposition", "").lower()
    assert "attachment" in cd
    assert "mydatasheet.pdf" in cd


def test_served_unknown_ext_forces_attachment_octet_stream():
    """A `.bin` (e.g. an upstream SVG that we refused) must download as
    application/octet-stream — never inline-render."""
    c = TestClient(app)
    ws_id = _signup(c)
    fname = _seed_asset(ws_id, b"<svg/>", "bin")
    r = c.get(f"/api/parts/assets/{ws_id}/{fname}")
    assert r.status_code == 200, r.text
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-type"].startswith("application/octet-stream")
    assert "attachment" in r.headers.get("content-disposition", "").lower()
