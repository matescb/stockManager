from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


# Magic-byte prefixes for the allow-listed types. The rest of the file
# can be arbitrary bytes — _detect_mime only inspects the first 16 bytes.
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff\xe0"
WEBP_MAGIC = b"RIFF\x10\x00\x00\x00WEBP"
PDF_MAGIC = b"%PDF-1.4\n"


def _png(payload: bytes = b"data") -> bytes:
    return PNG_MAGIC + payload


def _signup(c: TestClient, email: str | None = None) -> str:
    email = email or f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["workspace_id"]


@pytest.fixture
def authed():
    c = TestClient(app)
    _signup(c)
    return c


def _make_part(c: TestClient) -> str:
    return c.post(
        "/api/parts", json={"name": "Cap", "part_type": "local"}
    ).json()["data"]["id"]


# ---------------------------------------------------------------------------
# Happy path — upload / list / download / delete on the new MIME allow-list
# ---------------------------------------------------------------------------


def test_upload_list_download_delete(authed):
    part_id = _make_part(authed)
    body = _png(b"hello")
    r = authed.post(
        "/api/attachments",
        files={"file": ("photo.png", body, "image/png")},
        data={"object_type": "part", "object_id": part_id, "file_type": "datasheet"},
    )
    assert r.status_code == 201, r.text
    a = r.json()["data"]
    # Filename is stored AS sanitized — same input here, plus the canonical
    # extension derived from the validated MIME.
    assert a["file_name"] == "photo.png"
    assert a["object_type"] == "part"
    assert a["file_type"] == "datasheet"
    assert a["mime_type"] == "image/png"
    assert a["size_bytes"] == len(body)
    aid = a["id"]

    listed = authed.get(f"/api/attachments/by-object/part/{part_id}").json()["data"]
    assert len(listed) == 1
    assert listed[0]["id"] == aid

    dl = authed.get(f"/api/attachments/{aid}/download")
    assert dl.status_code == 200
    assert dl.content == body
    # Download must always use Content-Disposition: attachment so even
    # an allow-listed image cannot be inline-rendered as part of an XSS.
    cd = dl.headers.get("content-disposition", "").lower()
    assert "attachment" in cd, cd
    assert "filename=" in cd, cd

    rd = authed.delete(f"/api/attachments/{aid}")
    assert rd.status_code == 200
    assert authed.get(f"/api/attachments/by-object/part/{part_id}").json()["data"] == []


def test_workspace_isolation(authed):
    part_id = _make_part(authed)
    a = authed.post(
        "/api/attachments",
        files={"file": ("a.png", _png(b"secret"), "image/png")},
        data={"object_type": "part", "object_id": part_id, "file_type": "other"},
    ).json()["data"]
    aid = a["id"]

    other = TestClient(app)
    _signup(other)
    # Other workspace cannot see the attachment in its own listing
    listed = other.get(f"/api/attachments/by-object/part/{part_id}").json()["data"]
    assert listed == []

    # Direct download is denied as 404
    r = other.get(f"/api/attachments/{aid}/download")
    assert r.status_code == 404

    # Delete from another workspace also denied as 404
    r = other.delete(f"/api/attachments/{aid}")
    assert r.status_code == 404

    # Owner workspace still has it
    still = authed.get(f"/api/attachments/by-object/part/{part_id}").json()["data"]
    assert len(still) == 1


# ---------------------------------------------------------------------------
# MIME allow-list — Sec CRIT-1 (stored XSS via uploaded SVG / HTML)
# ---------------------------------------------------------------------------


def test_upload_rejects_text_html(authed):
    part_id = _make_part(authed)
    r = authed.post(
        "/api/attachments",
        files={"file": ("evil.html", b"<script>alert(1)</script>", "text/html")},
        data={"object_type": "part", "object_id": part_id, "file_type": "other"},
    )
    assert r.status_code == 415, r.text


def test_upload_rejects_svg(authed):
    """SVG is excluded from the allow-list because correctly sanitising
    embedded JS, event handlers, foreign-objects, etc. is research-grade."""
    part_id = _make_part(authed)
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    r = authed.post(
        "/api/attachments",
        files={"file": ("photo.svg", svg, "image/svg+xml")},
        data={"object_type": "part", "object_id": part_id, "file_type": "other"},
    )
    assert r.status_code == 415, r.text


def test_upload_rejects_text_plain(authed):
    part_id = _make_part(authed)
    r = authed.post(
        "/api/attachments",
        files={"file": ("note.txt", b"hello", "text/plain")},
        data={"object_type": "part", "object_id": part_id, "file_type": "other"},
    )
    assert r.status_code == 415, r.text


def test_upload_rejects_magic_byte_mismatch(authed):
    """`Content-Type: image/png` declared but actual content is HTML —
    must reject. Defeats the canonical evil.html-as-PNG attack."""
    part_id = _make_part(authed)
    r = authed.post(
        "/api/attachments",
        files={"file": ("photo.png", b"<html><script>alert(1)</script>", "image/png")},
        data={"object_type": "part", "object_id": part_id, "file_type": "other"},
    )
    assert r.status_code == 415, r.text


def test_upload_rejects_declared_mime_mismatch(authed):
    """Bytes are a real PNG, but the client declares JPEG. Even though
    the bytes are safe, mismatched declarations are an attempt to
    confuse the type-resolution path — reject defensively."""
    part_id = _make_part(authed)
    r = authed.post(
        "/api/attachments",
        files={"file": ("photo.jpg", _png(b"data"), "image/jpeg")},
        data={"object_type": "part", "object_id": part_id, "file_type": "other"},
    )
    assert r.status_code == 415, r.text


def test_upload_accepts_pdf(authed):
    part_id = _make_part(authed)
    r = authed.post(
        "/api/attachments",
        files={"file": ("ds.pdf", PDF_MAGIC + b"body", "application/pdf")},
        data={"object_type": "part", "object_id": part_id, "file_type": "datasheet"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["data"]["mime_type"] == "application/pdf"


def test_upload_accepts_webp(authed):
    part_id = _make_part(authed)
    r = authed.post(
        "/api/attachments",
        files={"file": ("photo.webp", WEBP_MAGIC + b"body", "image/webp")},
        data={"object_type": "part", "object_id": part_id, "file_type": "other"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["data"]["mime_type"] == "image/webp"


def test_upload_accepts_jpeg(authed):
    part_id = _make_part(authed)
    r = authed.post(
        "/api/attachments",
        files={"file": ("photo.jpg", JPEG_MAGIC + b"body", "image/jpeg")},
        data={"object_type": "part", "object_id": part_id, "file_type": "other"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["data"]["mime_type"] == "image/jpeg"


def test_upload_rejects_empty_file(authed):
    part_id = _make_part(authed)
    r = authed.post(
        "/api/attachments",
        files={"file": ("empty.png", b"", "image/png")},
        data={"object_type": "part", "object_id": part_id, "file_type": "other"},
    )
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# Size cap — Sec CRIT-2 (auth-required OOM via unbounded `await file.read()`)
# ---------------------------------------------------------------------------


def test_upload_rejects_over_size_cap(authed, monkeypatch):
    """Force a 10 KiB cap via env override so the test is fast, then
    upload 20 KiB and assert 413."""
    from app.core.config import settings

    monkeypatch.setenv("MAX_UPLOAD_BYTES", str(10 * 1024))
    settings.cache_clear()
    try:
        part_id = _make_part(authed)
        big = _png(b"X" * (20 * 1024))
        r = authed.post(
            "/api/attachments",
            files={"file": ("big.png", big, "image/png")},
            data={"object_type": "part", "object_id": part_id, "file_type": "other"},
        )
        assert r.status_code == 413, r.text
    finally:
        monkeypatch.delenv("MAX_UPLOAD_BYTES", raising=False)
        settings.cache_clear()


# ---------------------------------------------------------------------------
# Filename sanitization — path traversal, control chars, mixed extensions
# ---------------------------------------------------------------------------


def test_upload_sanitizes_path_traversal_filename(authed):
    part_id = _make_part(authed)
    r = authed.post(
        "/api/attachments",
        files={"file": ("../../etc/passwd.png", _png(b"data"), "image/png")},
        data={"object_type": "part", "object_id": part_id, "file_type": "other"},
    )
    assert r.status_code == 201, r.text
    name = r.json()["data"]["file_name"]
    # Slashes and dots must be stripped/replaced; canonical extension reasserted.
    assert "/" not in name
    assert ".." not in name
    assert name.endswith(".png")
    # The visible string must contain something derived from the input.
    assert "passwd" in name or "etc" in name or name.startswith("attachment-")


def test_upload_synthesises_filename_when_input_strips_to_empty(authed):
    """A filename that's all special chars (e.g. `~/`, `///`, `...`)
    sanitises to empty. The fallback synthesises an `attachment-<hex>.<ext>`
    name rather than 500'ing or producing a bare-extension filename."""
    part_id = _make_part(authed)
    r = authed.post(
        "/api/attachments",
        files={"file": ("///", _png(b"data"), "image/png")},
        data={"object_type": "part", "object_id": part_id, "file_type": "other"},
    )
    assert r.status_code == 201, r.text
    name = r.json()["data"]["file_name"]
    assert name.startswith("attachment-")
    assert name.endswith(".png")


def test_upload_overrides_extension_to_match_mime(authed):
    """The user names their file `evil.html` but the bytes + declared
    type are PNG. Stored extension must be `.png`, not `.html`."""
    part_id = _make_part(authed)
    r = authed.post(
        "/api/attachments",
        files={"file": ("evil.html", _png(b"data"), "image/png")},
        data={"object_type": "part", "object_id": part_id, "file_type": "other"},
    )
    assert r.status_code == 201, r.text
    name = r.json()["data"]["file_name"]
    assert name.endswith(".png")
    assert ".html" not in name


# ---------------------------------------------------------------------------
# Download Content-Disposition — even with allow-listed MIMEs, force download
# ---------------------------------------------------------------------------


def test_download_forces_attachment_disposition(authed):
    part_id = _make_part(authed)
    a = authed.post(
        "/api/attachments",
        files={"file": ("photo.png", _png(b"data"), "image/png")},
        data={"object_type": "part", "object_id": part_id, "file_type": "other"},
    ).json()["data"]
    dl = authed.get(f"/api/attachments/{a['id']}/download")
    assert dl.status_code == 200
    cd = dl.headers.get("content-disposition", "").lower()
    assert cd.startswith("attachment"), cd
    # No matter what MIME is served, the browser sees an explicit "save"
    # rather than "render inline" — the XSS-via-image-tag escape is closed.
    assert "inline" not in cd


def test_download_legacy_unsupported_mime_falls_back_to_octet_stream(authed):
    """Pre-existing prod attachments uploaded before the allow-list
    landed may carry `mime_type='text/html'` or NULL. Download must
    still work but serve them as `application/octet-stream` so the
    browser cannot inline-render them."""
    part_id = _make_part(authed)
    a = authed.post(
        "/api/attachments",
        files={"file": ("photo.png", _png(b"data"), "image/png")},
        data={"object_type": "part", "object_id": part_id, "file_type": "other"},
    ).json()["data"]

    # Backdoor: rewrite the stored mime_type to a legacy value to simulate
    # an attachment that predates this PR.
    from sqlalchemy import update
    from app.domain.attachments.models import Attachment
    from app.infra.db import SessionLocal

    with SessionLocal() as s:
        s.execute(update(Attachment).where(Attachment.id == uuid.UUID(a["id"])).values(mime_type="text/html"))
        s.commit()

    dl = authed.get(f"/api/attachments/{a['id']}/download")
    assert dl.status_code == 200
    assert dl.headers.get("content-type", "").startswith("application/octet-stream")
    cd = dl.headers.get("content-disposition", "").lower()
    assert cd.startswith("attachment")
