"""`/api/eda/{symbols,footprints}/{id}/preview.svg` — the 2D preview images.

The CAD tab draws a hosted symbol or footprint exactly as KiCad does, by
rendering it through kicad-cli in the `kicad-render` sidecar and serving
the SVG (`domain/eda/render.py`). The sidecar is a separate container and
is not running under pytest, so these route tests stub the one call that
crosses to it (`render._render_via_sidecar`) with a canned SVG and assert
on the HTTP contract — status, `image/svg+xml`, the security headers, the
cache behaviour, workspace isolation, and the degrade-to-503 when the
sidecar is down. The fidelity of the actual kicad-cli output is proven out
of band (the switch's spike), the same way `test_eda_preview3d.py` leaves
the true render to the build.

Isolation follows the house pattern (`test_eda.py`): a second signup gets
a second workspace and every cross-workspace id must come back 404.
"""
from __future__ import annotations

import os
import shutil
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.domain.eda import render
from app.main import app
from tests._factories import signup_user

# A minimal but real SVG the stubbed sidecar returns. Opens with the XML
# prolog so it passes `render`'s own is-this-an-SVG check.
CANNED_SVG = (
    b'<?xml version="1.0" standalone="no"?>\n'
    b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
    b'<rect width="10" height="10"/></svg>\n'
)


# ---------------------------------------------------------------------
# Fixture content + helpers
# ---------------------------------------------------------------------


def _symbol_text(name: str = "R") -> str:
    return (
        f'(symbol "{name}" (in_bom yes) (on_board yes)\n'
        f'  (property "Reference" "R" (at 0 0 0))\n'
        f'  (property "Value" "{name}" (at 0 0 0))\n'
        f'  (symbol "{name}_0_1"\n'
        f"    (rectangle (start -1 -2.54) (end 1 2.54))\n"
        f"  )\n"
        f")\n"
    )


def _footprint_text(name: str = "R_0402") -> str:
    return (
        f'(footprint "{name}" (layer "F.Cu")\n'
        f'  (descr "test")\n'
        f'  (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask"))\n'
        f")\n"
    )


@pytest.fixture
def other_client(db):
    """A second workspace, for the cross-workspace probes."""
    c = TestClient(app)
    signup_user(c)
    return c


@pytest.fixture
def sidecar(monkeypatch):
    """Stub the sidecar call. Exposes a call counter and a settable outcome.

    Default: returns `CANNED_SVG`. Set `state["raise"]` to a `RenderError`
    to simulate a sidecar that is down or could not render.
    """
    state = {"calls": 0, "raise": None, "svg": CANNED_SVG}

    def fake_render(kind: str, payload: bytes) -> bytes:
        state["calls"] += 1
        if state["raise"] is not None:
            raise state["raise"]
        return state["svg"]

    monkeypatch.setattr(render, "_render_via_sidecar", fake_render)
    return state


def _clear_preview_cache() -> None:
    """Drop every workspace's on-disk preview cache so a test starts cold."""
    root = os.path.join(settings().UPLOAD_DIR, "eda")
    if not os.path.isdir(root):
        return
    for ws_dir in os.listdir(root):
        shutil.rmtree(os.path.join(root, ws_dir, "preview"), ignore_errors=True)


def _upload(client, path: str, filename: str, content: str, **form):
    data = {k: str(v) for k, v in form.items() if v is not None}
    r = client.post(
        path,
        files={"file": (filename, content.encode("utf-8"), "application/octet-stream")},
        data=data,
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _upload_symbol(client, entry: str = "R", **form):
    return _upload(
        client, "/api/eda/symbols", f"{entry}.kicad_sym", _symbol_text(entry), **form
    )


def _upload_footprint(client, entry: str = "R_0402", **form):
    return _upload(
        client,
        "/api/eda/footprints",
        f"{entry}.kicad_mod",
        _footprint_text(entry),
        **form,
    )


def _symbol_preview(client, symbol_id):
    return client.get(f"/api/eda/symbols/{symbol_id}/preview.svg")


def _footprint_preview(client, footprint_id):
    return client.get(f"/api/eda/footprints/{footprint_id}/preview.svg")


# ---------------------------------------------------------------------
# Symbol previews
# ---------------------------------------------------------------------


def test_symbol_preview_returns_svg(authed_client, sidecar):
    row = _upload_symbol(authed_client, "R")
    r = _symbol_preview(authed_client, row["id"])
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/svg+xml"
    assert r.content.lstrip().startswith(b"<?xml")


def test_symbol_preview_sends_a_one_symbol_library_to_the_sidecar(
    authed_client, sidecar, monkeypatch
):
    """The stored bare `(symbol …)` must reach the sidecar wrapped in a
    one-symbol `(kicad_symbol_lib …)` — the format kicad-cli reads."""
    _clear_preview_cache()
    seen = {}

    def capture(kind: str, payload: bytes) -> bytes:
        seen["kind"] = kind
        seen["payload"] = payload
        return CANNED_SVG

    monkeypatch.setattr(render, "_render_via_sidecar", capture)

    row = _upload_symbol(authed_client, "R")
    assert _symbol_preview(authed_client, row["id"]).status_code == 200
    assert seen["kind"] == "symbol"
    assert seen["payload"].startswith(b"(kicad_symbol_lib")
    assert b'(symbol "R"' in seen["payload"]


def test_symbol_preview_headers(authed_client, sidecar):
    """`nosniff` + a private cache: the SVG is kicad-cli output derived from
    stored geometry, served from our own origin and workspace-scoped. The
    client renders it via <img>, so script inside it can never run."""
    row = _upload_symbol(authed_client, "R")
    r = _symbol_preview(authed_client, row["id"])
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "private, max-age=300"
    # Defence in depth if the SVG URL is navigated to directly (SEC2-009):
    # `default-src 'none'` + `sandbox` leaves no way for embedded script to
    # run, and framing is denied.
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "sandbox" in csp
    assert r.headers["x-frame-options"] == "DENY"


def test_symbol_preview_is_cached_on_the_second_request(authed_client, sidecar):
    """A repeat hit is served from disk without touching the sidecar again."""
    _clear_preview_cache()
    row = _upload_symbol(authed_client, "R")
    assert _symbol_preview(authed_client, row["id"]).status_code == 200
    assert _symbol_preview(authed_client, row["id"]).status_code == 200
    assert sidecar["calls"] == 1, "second request should not re-render"


def test_symbol_preview_503_when_sidecar_down(authed_client, sidecar):
    _clear_preview_cache()
    sidecar["raise"] = render.RenderUnavailable("down")
    row = _upload_symbol(authed_client, "R")
    r = _symbol_preview(authed_client, row["id"])
    assert r.status_code == 503, r.text
    assert r.json()["code"] == "eda.preview_unavailable"


def test_archived_symbol_still_previews(authed_client, sidecar):
    """The restore flow depends on it: deciding whether to bring an
    archived symbol back means seeing what it is."""
    row = _upload_symbol(authed_client, "R")
    assert authed_client.post(f"/api/eda/symbols/{row['id']}/archive").status_code == 200
    r = _symbol_preview(authed_client, row["id"])
    assert r.status_code == 200, r.text


def test_symbol_preview_unknown_id_404(authed_client, sidecar):
    r = _symbol_preview(authed_client, uuid.uuid4())
    assert r.status_code == 404
    assert r.json()["code"] == "eda_symbol.not_found"


def test_symbol_preview_is_workspace_isolated(authed_client, other_client, sidecar):
    row = _upload_symbol(authed_client, "R")
    r = _symbol_preview(other_client, row["id"])
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "eda_symbol.not_found"


# ---------------------------------------------------------------------
# Footprint previews
# ---------------------------------------------------------------------


def test_footprint_preview_returns_svg(authed_client, sidecar):
    row = _upload_footprint(authed_client, "R_0402")
    r = _footprint_preview(authed_client, row["id"])
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/svg+xml"
    assert r.content.lstrip().startswith(b"<?xml")


def test_footprint_preview_sends_the_raw_footprint_to_the_sidecar(
    authed_client, sidecar, monkeypatch
):
    """A footprint is already a complete `(footprint …)` node — it goes to
    the sidecar verbatim, no wrapper."""
    _clear_preview_cache()
    seen = {}

    def capture(kind: str, payload: bytes) -> bytes:
        seen["kind"] = kind
        seen["payload"] = payload
        return CANNED_SVG

    monkeypatch.setattr(render, "_render_via_sidecar", capture)

    row = _upload_footprint(authed_client, "R_0402")
    assert _footprint_preview(authed_client, row["id"]).status_code == 200
    assert seen["kind"] == "footprint"
    assert seen["payload"].startswith(b"(footprint")


def test_footprint_preview_headers(authed_client, sidecar):
    row = _upload_footprint(authed_client, "R_0402")
    r = _footprint_preview(authed_client, row["id"])
    assert r.headers["content-type"] == "image/svg+xml"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "private, max-age=300"


def test_footprint_preview_503_when_sidecar_down(authed_client, sidecar):
    _clear_preview_cache()
    sidecar["raise"] = render.RenderFailed("kicad-cli error")
    row = _upload_footprint(authed_client, "R_0402")
    r = _footprint_preview(authed_client, row["id"])
    assert r.status_code == 503, r.text
    assert r.json()["code"] == "eda.preview_unavailable"


def test_archived_footprint_still_previews(authed_client, sidecar):
    row = _upload_footprint(authed_client, "R_0402")
    assert (
        authed_client.post(f"/api/eda/footprints/{row['id']}/archive").status_code == 200
    )
    r = _footprint_preview(authed_client, row["id"])
    assert r.status_code == 200, r.text


def test_footprint_preview_unknown_id_404(authed_client, sidecar):
    r = _footprint_preview(authed_client, uuid.uuid4())
    assert r.status_code == 404
    assert r.json()["code"] == "eda_footprint.not_found"


def test_footprint_preview_is_workspace_isolated(authed_client, other_client, sidecar):
    row = _upload_footprint(authed_client, "R_0402")
    r = _footprint_preview(other_client, row["id"])
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "eda_footprint.not_found"


# ---------------------------------------------------------------------
# Preview images are not audited
# ---------------------------------------------------------------------


def test_preview_is_a_read_and_writes_no_audit_row(authed_client, db, sidecar):
    """Guards against someone "fixing" the audit-coverage test by adding a
    log call here: these are GETs, and the audit table is for mutations."""
    from sqlalchemy import func, select

    from app.domain.audit.models import AuditLog

    symbol = _upload_symbol(authed_client, "R")
    footprint = _upload_footprint(authed_client, "R_0402")
    before = db.execute(select(func.count()).select_from(AuditLog)).scalar_one()

    _symbol_preview(authed_client, symbol["id"])
    _footprint_preview(authed_client, footprint["id"])

    after = db.execute(select(func.count()).select_from(AuditLog)).scalar_one()
    assert after == before
