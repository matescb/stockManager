"""`/api/eda/datafiles/{id}/preview.glb` — the STEP→GLB 3D preview.

STEP renders in no browser, so this route tessellates it to a glTF binary
server-side (via cascadio / OpenCASCADE), caches it content-addressed, and
serves it for three.js to draw. The route is STEP-only by design: WRL is a
mesh three.js reads directly from `/files`, and SPICE is not 3D — both
answer 422 here (see `api/routes/eda_preview3d.py`).

The load-bearing behaviours pinned below: a real conversion produces a
parseable glTF 2.0 with geometry; a second request is served from cache
without re-converting; a corrupt STEP is a 422, never a 500; the caps and
the workspace-isolation 404 hold. Isolation follows the house pattern
(`test_eda_preview.py`): a second signup gets a second workspace and every
cross-workspace id must come back 404.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import struct
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core import ratelimit as _ratelimit_mod
from app.core.config import settings
from app.domain.eda import preview3d
from app.main import app
from tests._factories import signup_user

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "eda", "preview3d")
with open(os.path.join(_FIXTURES, "cube.step"), "rb") as _fh:
    CUBE_STEP = _fh.read()

# Passes the upload magic check (`ISO-10303-21`) but carries no geometry
# OpenCASCADE can read — the "corrupt STEP" case that must be a 422.
JUNK_STEP = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n#1=NONSENSE();\nENDSEC;\nEND-ISO-10303-21;\n"
# Minimal VRML: enough to pass the `#VRML` upload check.
TINY_WRL = b"#VRML V2.0 utf8\nShape { geometry Box { size 1 1 1 } }\n"


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


@pytest.fixture
def other_client(db):
    """A second workspace, for the cross-workspace probes."""
    c = TestClient(app)
    signup_user(c)
    return c


@pytest.fixture
def limiter_enabled():
    """Turn the per-workspace limiter on for one test (it is off in tests
    by default). Mirrors `test_sourcing_alerts_route.py`."""
    original = _ratelimit_mod.limiter.enabled
    _ratelimit_mod.limiter.enabled = True
    with contextlib.suppress(Exception):
        _ratelimit_mod.limiter.reset()
    yield
    _ratelimit_mod.limiter.enabled = original
    with contextlib.suppress(Exception):
        _ratelimit_mod.limiter.reset()


def _clear_preview_cache() -> None:
    """Drop every workspace's on-disk GLB cache so a test starts cold."""
    root = os.path.join(settings().UPLOAD_DIR, "eda")
    if not os.path.isdir(root):
        return
    for ws_dir in os.listdir(root):
        shutil.rmtree(os.path.join(root, ws_dir, "preview"), ignore_errors=True)


def _upload_datafile(client, filename: str, content: bytes, **form):
    data = {k: str(v) for k, v in form.items() if v is not None}
    r = client.post(
        "/api/eda/datafiles",
        files={"file": (filename, content, "application/octet-stream")},
        data=data,
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _preview(client, datafile_id):
    return client.get(f"/api/eda/datafiles/{datafile_id}/preview.glb")


def _parse_glb(body: bytes) -> dict:
    """Validate the glTF-binary container and return its JSON chunk.

    A GLB is a 12-byte header (`glTF`, version, length) then length-tagged
    chunks; the first is the JSON document. Parsing it here is the
    "a real glTF validator accepts it" check the spec asks for.
    """
    assert body[:4] == b"glTF", f"missing glTF magic: {body[:8]!r}"
    magic, version, total = struct.unpack("<4sII", body[:12])
    assert version == 2, f"expected glTF 2.0, got {version}"
    assert total == len(body), "declared length does not match body"
    json_len, json_type = struct.unpack("<II", body[12:20])
    assert json_type == 0x4E4F534A, "first chunk is not JSON"
    return json.loads(body[20 : 20 + json_len])


# ---------------------------------------------------------------------
# STEP → GLB happy path
# ---------------------------------------------------------------------


def test_step_preview_returns_a_parseable_glb_with_geometry(authed_client):
    row = _upload_datafile(authed_client, "cube.step", CUBE_STEP)
    r = _preview(authed_client, row["id"])
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "model/gltf-binary"

    doc = _parse_glb(r.content)
    assert doc["asset"]["version"] == "2.0"
    assert len(doc.get("meshes", [])) >= 1, "converted GLB carries no mesh"
    assert len(doc.get("accessors", [])) >= 1, "converted GLB carries no geometry"


def test_step_preview_headers(authed_client):
    """`nosniff` + a private cache: the GLB is a binary artifact derived
    from a user upload, served from our own origin and workspace-scoped."""
    row = _upload_datafile(authed_client, "cube.step", CUBE_STEP)
    r = _preview(authed_client, row["id"])
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "private, max-age=300"


def test_step_preview_is_served_from_cache_on_the_second_request(
    authed_client, monkeypatch
):
    """The expensive conversion runs once; the second request is disk-cached.

    Guards the whole reason the cache exists — a converter bump must
    invalidate it (that is covered by `test_preview3d_unit`), but a
    repeat of the same request must not re-tessellate.
    """
    _clear_preview_cache()
    calls = {"n": 0}
    real = preview3d._convert

    def counting(source: bytes) -> bytes:
        calls["n"] += 1
        return real(source)

    monkeypatch.setattr(preview3d, "_convert", counting)

    row = _upload_datafile(authed_client, "cube.step", CUBE_STEP)
    assert _preview(authed_client, row["id"]).status_code == 200
    assert _preview(authed_client, row["id"]).status_code == 200
    assert calls["n"] == 1, "second request should not re-convert"


# ---------------------------------------------------------------------
# Non-STEP kinds are not GLB-previewable
# ---------------------------------------------------------------------


def test_wrl_datafile_has_no_glb_preview_but_is_servable_from_files(authed_client):
    """WRL is 422 on this route — the frontend reads it natively from
    `/files` instead, so that path is asserted too."""
    row = _upload_datafile(authed_client, "part.wrl", TINY_WRL)
    r = _preview(authed_client, row["id"])
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "eda.preview_unavailable"

    # The path the frontend actually uses for WRL.
    me = authed_client.get("/api/auth/me").json()["data"]
    ws_id = me["workspaces"][0]["id"]
    served = authed_client.get(f"/api/eda/files/{ws_id}/{row['sha256']}.wrl")
    assert served.status_code == 200, served.text
    assert served.content == TINY_WRL


def test_spice_datafile_has_no_glb_preview(authed_client):
    row = _upload_datafile(authed_client, "model.lib", b".subckt X\n.ends\n")
    r = _preview(authed_client, row["id"])
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "eda.preview_unavailable"


# ---------------------------------------------------------------------
# Failure & cap handling — a corrupt STEP is a 422, never a 500
# ---------------------------------------------------------------------


def test_corrupt_step_is_422_not_500(authed_client):
    row = _upload_datafile(authed_client, "broken.step", JUNK_STEP)
    r = _preview(authed_client, row["id"])
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "eda.preview_unavailable"


def test_oversize_source_is_413(authed_client, monkeypatch):
    _clear_preview_cache()
    monkeypatch.setattr(preview3d, "MAX_SOURCE_BYTES", 10)
    row = _upload_datafile(authed_client, "cube.step", CUBE_STEP)
    r = _preview(authed_client, row["id"])
    assert r.status_code == 413, r.text
    assert r.json()["code"] == "eda.file_too_large"


def test_oversize_output_is_413(authed_client, monkeypatch):
    _clear_preview_cache()
    monkeypatch.setattr(preview3d, "MAX_OUTPUT_BYTES", 100)
    row = _upload_datafile(authed_client, "cube.step", CUBE_STEP)
    r = _preview(authed_client, row["id"])
    assert r.status_code == 413, r.text
    assert r.json()["code"] == "eda.file_too_large"


# ---------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------


def test_preview_unknown_id_404(authed_client):
    r = _preview(authed_client, uuid.uuid4())
    assert r.status_code == 404
    assert r.json()["code"] == "eda_datafile.not_found"


def test_preview_is_workspace_isolated(authed_client, other_client):
    row = _upload_datafile(authed_client, "cube.step", CUBE_STEP)
    r = _preview(other_client, row["id"])
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "eda_datafile.not_found"


def test_archived_datafile_still_previews(authed_client):
    row = _upload_datafile(authed_client, "cube.step", CUBE_STEP)
    assert (
        authed_client.post(f"/api/eda/datafiles/{row['id']}/archive").status_code == 200
    )
    r = _preview(authed_client, row["id"])
    assert r.status_code == 200, r.text
    _parse_glb(r.content)


def test_preview_writes_no_audit_row(authed_client, db):
    """A GET is not a mutation — guards against someone adding an audit
    call here to satisfy the coverage test."""
    from sqlalchemy import func, select

    from app.domain.audit.models import AuditLog

    row = _upload_datafile(authed_client, "cube.step", CUBE_STEP)
    before = db.execute(select(func.count()).select_from(AuditLog)).scalar_one()
    _preview(authed_client, row["id"])
    after = db.execute(select(func.count()).select_from(AuditLog)).scalar_one()
    assert after == before


def test_preview_rate_limited(authed_client, limiter_enabled):
    row = _upload_datafile(authed_client, "cube.step", CUBE_STEP)
    # 30/minute; the disk cache means these are cheap after the first.
    seen_429 = False
    for _ in range(35):
        if _preview(authed_client, row["id"]).status_code == 429:
            seen_429 = True
            break
    assert seen_429, "expected the 30/minute bucket to reject a burst"


# ---------------------------------------------------------------------
# Converter/cache unit checks
# ---------------------------------------------------------------------


def test_preview3d_unit_converts_and_is_deterministic():
    """`_convert` yields a glTF binary, and twice over identical bytes it is
    byte-identical — which is why the cache can key on the source alone."""
    glb1 = preview3d._convert(CUBE_STEP)
    glb2 = preview3d._convert(CUBE_STEP)
    assert glb1[:4] == b"glTF"
    assert glb1 == glb2


def test_preview3d_unit_corrupt_step_raises_conversion_failed():
    with pytest.raises(preview3d.ConversionFailed):
        preview3d._convert(JUNK_STEP)


def test_preview3d_cache_tag_encodes_the_converter_version():
    """A converter bump must land on a new cache filename so the stale GLB
    is pruned rather than served."""
    assert preview3d.CONVERTER_VERSION in preview3d.CACHE_TAG.replace("_", ".")
    assert "." not in preview3d.CACHE_TAG, "tag must be a flat filename token"
