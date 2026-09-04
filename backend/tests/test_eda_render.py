"""Unit coverage for `app/domain/eda/render.py` — the kicad-cli SVG client.

The route-level contract is in `test_eda_preview.py`; this pins the module
in isolation, with no DB and the sidecar HTTP call (`httpx.post`) stubbed:
the symbol wrapper, the payload shaping, the sidecar error taxonomy, the
content-addressed cache (hit, prune, in-memory fallback) and the caps.
"""
from __future__ import annotations

import os
import uuid

import pytest

from app.core.config import settings
from app.domain.eda import render, sexpr

CANNED = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>\n'


class _FakeResp:
    def __init__(self, status_code: int, content: bytes):
        self.status_code = status_code
        self.content = content


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Point UPLOAD_DIR at a scratch dir and the sidecar at a dummy URL."""
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("EDA_RENDER_URL", "http://kicad-render:8080")
    settings.cache_clear()
    yield tmp_path
    settings.cache_clear()


# ---------------------------------------------------------------------
# Payload shaping
# ---------------------------------------------------------------------


def test_wrap_symbol_is_a_valid_one_symbol_library():
    bare = b'(symbol "R" (property "Reference" "R" (at 0 0 0)))'
    lib = render._wrap_symbol(bare)
    assert lib.startswith(b"(kicad_symbol_lib")
    # kicad-cli reads a library; the wrap has to parse as one carrying our
    # single symbol.
    entries = sexpr.entries(lib.decode("utf-8"))
    assert [name for name, _ in entries] == ["R"]


def test_payload_for_wraps_symbol_but_passes_footprint_raw():
    bare = b'(symbol "R")'
    assert render._payload_for("symbol", bare).startswith(b"(kicad_symbol_lib")
    fp = b'(footprint "R_0402")'
    assert render._payload_for("footprint", fp) == fp


def test_cache_tag_is_a_flat_token_encoding_the_series():
    """A KiCad-series bump must land on a new cache filename, and the tag
    must stay a flat filename token (no dot that reads as an extension)."""
    assert "." not in render.CACHE_TAG
    assert render.KICAD_SERIES.replace(".", "_") in render.CACHE_TAG


# ---------------------------------------------------------------------
# Sidecar error taxonomy
# ---------------------------------------------------------------------


def test_transport_error_is_render_unavailable(env, monkeypatch):
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(render.httpx, "post", boom)
    with pytest.raises(render.RenderUnavailable):
        render._render_via_sidecar("symbol", b"x")


def test_non_200_is_render_failed(env, monkeypatch):
    monkeypatch.setattr(render.httpx, "post", lambda *a, **k: _FakeResp(502, b"nope"))
    with pytest.raises(render.RenderFailed):
        render._render_via_sidecar("symbol", b"x")


def test_non_svg_body_is_render_failed(env, monkeypatch):
    monkeypatch.setattr(
        render.httpx, "post", lambda *a, **k: _FakeResp(200, b"<!DOCTYPE html>")
    )
    with pytest.raises(render.RenderFailed):
        render._render_via_sidecar("footprint", b"x")


def test_svg_body_is_returned(env, monkeypatch):
    monkeypatch.setattr(render.httpx, "post", lambda *a, **k: _FakeResp(200, CANNED))
    assert render._render_via_sidecar("footprint", b"x") == CANNED


# ---------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------


def test_renders_once_then_serves_from_disk(env, monkeypatch):
    calls = {"n": 0}

    def fake(*a, **k):
        calls["n"] += 1
        return _FakeResp(200, CANNED)

    monkeypatch.setattr(render.httpx, "post", fake)
    ws, sha = uuid.uuid4(), "a" * 64
    p1, d1 = render.get_or_build_svg(ws, sha, b'(symbol "R")', "symbol")
    assert p1 is not None and d1 is None
    with open(p1, "rb") as fh:
        assert fh.read() == CANNED
    p2, _ = render.get_or_build_svg(ws, sha, b'(symbol "R")', "symbol")
    assert p2 == p1
    assert calls["n"] == 1, "second call must not re-render"


def test_empty_source_is_source_empty(env):
    with pytest.raises(render.SourceEmpty):
        render.get_or_build_svg(uuid.uuid4(), "c" * 64, b"", "symbol")


def test_oversize_output_is_refused(env, monkeypatch):
    monkeypatch.setattr(render.httpx, "post", lambda *a, **k: _FakeResp(200, CANNED))
    monkeypatch.setattr(render, "MAX_OUTPUT_BYTES", 3)
    with pytest.raises(render.OutputTooLarge):
        render.get_or_build_svg(uuid.uuid4(), "b" * 64, b"x", "footprint")


def test_in_memory_when_cache_dir_unwritable(env, monkeypatch):
    monkeypatch.setattr(render.httpx, "post", lambda *a, **k: _FakeResp(200, CANNED))
    monkeypatch.setattr(render, "_write_cache", lambda *a, **k: False)
    path, data = render.get_or_build_svg(uuid.uuid4(), "d" * 64, b"x", "footprint")
    assert path is None and data == CANNED


def test_prune_drops_superseded_tag_but_keeps_the_glb(env, monkeypatch):
    """A fresh render prunes an older-tag SVG for the same source, but must
    never touch the GLB the 3D preview caches in the same dir."""
    monkeypatch.setattr(render.httpx, "post", lambda *a, **k: _FakeResp(200, CANNED))
    ws, sha = uuid.uuid4(), "e" * 64
    cache_dir = render._cache_dir(ws)
    os.makedirs(cache_dir, exist_ok=True)
    stale_svg = os.path.join(cache_dir, f"{sha}.v0-kicadcli8_0.svg")
    sibling_glb = os.path.join(cache_dir, f"{sha}.v1-cascadio0_1_1.glb")
    with open(stale_svg, "wb") as fh:
        fh.write(b"old")
    with open(sibling_glb, "wb") as fh:
        fh.write(b"glTFxxxx")

    render.get_or_build_svg(ws, sha, b"x", "footprint")

    assert not os.path.exists(stale_svg), "superseded-tag SVG should be pruned"
    assert os.path.exists(sibling_glb), "the 3D GLB cache must be left alone"
