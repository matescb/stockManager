"""kicad-cli SVG rendering for the CAD tab's 2D previews, with a disk cache.

The binary sibling of this is `preview3d.py` (STEP→GLB); this is the 2D
half. A symbol or footprint must be drawn exactly the way KiCad draws it,
and the only faithful renderer of a KiCad document is KiCad — so the
geometry is handed to `kicad-cli` running in the `kicad-render` sidecar
(see `render/`) and the SVG it returns is cached.

Why a sidecar and not in-process
--------------------------------

`kicad-cli` is a ~200 MB KiCad install; it has no place in the slim
backend image. It also renders by shelling out, which the request path
must not do. So it lives in its own container and this module talks to it
over the internal docker network (`settings().EDA_RENDER_URL`). If that
container is down the previews degrade to a 503 "preview unavailable" —
the app never 500s on a missing sidecar.

What gets sent
--------------

The sidecar renders whole documents kicad-cli can open. A footprint is
already a complete `(footprint …)` node, sent verbatim. A symbol is a
bare `(symbol …)` entry in the store, so `_wrap_symbol` puts it in a
one-symbol `(kicad_symbol_lib …)` first — a plain string wrap, no parse,
because the stored bytes are already the canonical re-emitted entry (the
spike confirmed this renders byte-for-byte the same as the original
library). Unlike `preview.py`'s KiCanvas wrapper this is the format KiCad
itself reads, which is the whole point of the switch.

The cache
---------

Rendering is deterministic in (source bytes, kicad-cli version), and the
source is immutable (content-addressed by the entry's `sha256`), so the
SVG is cached at

    {UPLOAD_DIR}/eda/{ws_id}/preview/{source_sha}.{CACHE_TAG}.svg

next to the GLB cache `preview3d.py` writes (distinct extension and tag,
distinct `source_sha` per entry, so the two never collide). `CACHE_TAG`
embeds the pinned KiCad series, so bumping the sidecar image lands every
render on a new filename and prunes the stale one — the same "a version
change must invalidate" rule `preview3d.py` and `pcm.py` follow. The write
idiom (unique scratch file then `os.replace`, prune superseded tags for
the same source) mirrors those two exactly.
"""
from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import time

import httpx

from app.core.config import settings

__all__ = [
    "RENDERER",
    "KICAD_SERIES",
    "CACHE_TAG",
    "MAX_OUTPUT_BYTES",
    "RenderError",
    "SourceEmpty",
    "RenderUnavailable",
    "RenderFailed",
    "OutputTooLarge",
    "get_or_build_svg",
]

_log = logging.getLogger(__name__)

# The renderer identity. `KICAD_SERIES` must track the KiCad major.minor
# pinned in `render/Dockerfile`; when that image is bumped, bump this and
# every cached SVG lands on a new filename. Bump `_CACHE_SCHEME` instead to
# invalidate without a version change (e.g. if the CLI flags change).
RENDERER = "kicadcli"
KICAD_SERIES = "9.0"
_CACHE_SCHEME = "v1"
CACHE_TAG = f"{_CACHE_SCHEME}-{RENDERER}{KICAD_SERIES.replace('.', '_')}"

_MIB = 1024 * 1024
# A symbol SVG runs ~200 KiB and a footprint ~30 KiB in practice; this cap
# is a guard against a pathological entry meshing into megabytes of paths,
# not a real limit. Refuse rather than stream tens of MiB to a browser tab.
MAX_OUTPUT_BYTES = 8 * _MIB

# The sidecar endpoint per stored kind.
_ENDPOINT_BY_KIND = {"symbol": "/render/symbol", "footprint": "/render/footprint"}

# Connect fast (the sidecar is on the same docker network), but allow a
# generous read: a cold render shells out to kicad-cli, which the sidecar
# itself caps at 30 s.
_TIMEOUT = httpx.Timeout(connect=5.0, read=35.0, write=10.0, pool=5.0)

# A single-symbol library header. The version is a recent schematic-symbol
# format stamp; kicad-cli only reads it as a compatibility hint and renders
# the same regardless, so it need not track KICAD_SERIES.
_SYMBOL_LIB_HEADER = '(kicad_symbol_lib (version 20241209) (generator "stockmanager")\n'


class RenderError(Exception):
    """Base for the failures the route maps onto HTTP status codes."""


class SourceEmpty(RenderError):
    """The stored source blob is empty — a 'can't happen' for a valid entry."""


class RenderUnavailable(RenderError):
    """The render sidecar could not be reached (down, timeout) → 503."""


class RenderFailed(RenderError):
    """The sidecar reached kicad-cli but no SVG came back → 503."""


class OutputTooLarge(RenderError):
    """The rendered SVG exceeds `MAX_OUTPUT_BYTES`; refused after render."""


def _wrap_symbol(source: bytes) -> bytes:
    """Wrap a bare `(symbol …)` entry into a one-symbol `.kicad_sym` library."""
    return _SYMBOL_LIB_HEADER.encode("utf-8") + source.rstrip() + b"\n)\n"


def _payload_for(kind: str, source: bytes) -> bytes:
    """The exact bytes to POST for `kind` — symbols are wrapped, footprints raw."""
    return _wrap_symbol(source) if kind == "symbol" else source


def _render_via_sidecar(kind: str, payload: bytes) -> bytes:
    """POST `payload` to the sidecar and return the SVG bytes, or raise.

    `RenderUnavailable` for a transport fault (the sidecar is down), and
    `RenderFailed` for a reached-but-unhappy sidecar (kicad-cli error, or a
    body that is not an SVG). The route answers both with 503, but they are
    distinct so logs can tell "no sidecar" from "sidecar could not render".
    """
    url = settings().EDA_RENDER_URL.rstrip("/") + _ENDPOINT_BY_KIND[kind]
    try:
        response = httpx.post(url, content=payload, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        raise RenderUnavailable(f"render sidecar unreachable: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise RenderFailed(f"render sidecar returned {response.status_code}")
    svg = response.content
    # A real SVG opens with the XML prolog or the root element; anything
    # else means the sidecar handed back an error page or empty body.
    head = svg[:64].lstrip()
    if not (head.startswith(b"<?xml") or head.startswith(b"<svg")):
        raise RenderFailed("render sidecar returned a non-SVG body")
    return svg


def _cache_dir(workspace_id) -> str:
    return os.path.join(settings().UPLOAD_DIR, "eda", str(workspace_id), "preview")


def _cache_name(source_sha: str) -> str:
    return f"{source_sha}.{CACHE_TAG}.svg"


# Grace before a scratch file is treated as debris — long enough that an
# in-flight write is never touched, short enough that a killed process
# leaves nothing behind for long. Mirrors `preview3d._TMP_GRACE_SECONDS`.
_TMP_GRACE_SECONDS = 3600
_SCRATCH_SUFFIX = ".svg.tmp"


def _write_cache(cache_dir: str, cache_path: str, data: bytes) -> bool:
    """Write `data` to `cache_path` atomically. False if the dir is unusable.

    Same idiom as `preview3d._write_cache` / `storage.store`: a uniquely
    named scratch file then `os.replace`, so a reader never sees a partial
    SVG and a crashed write leaves only a prunable `.tmp`.
    """
    try:
        os.makedirs(cache_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=cache_dir, suffix=_SCRATCH_SUFFIX)
    except OSError:
        _log.warning("eda 2d preview: cache dir unusable (%s)", cache_dir)
        return False
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_path, cache_path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        _log.warning("eda 2d preview: could not write cache file (%s)", cache_path)
        return False
    return True


def _prune(cache_dir: str, source_sha: str, keep: str) -> None:
    """Drop superseded-tag SVGs for `source_sha` and stale scratch files.

    Scoped to the SVGs this module writes: a `{source_sha}.*.svg` that is
    not the current tag is a leftover from an older kicad-cli series and is
    removed; the GLB cache (`.glb`) and every other entry's SVG (different
    `source_sha` prefix) are left untouched.
    """
    now = time.time()
    prefix = f"{source_sha}."
    try:
        entries = os.listdir(cache_dir)
    except OSError:
        return
    for name in entries:
        path = os.path.join(cache_dir, name)
        if name == keep:
            continue
        if name.endswith(_SCRATCH_SUFFIX):
            with contextlib.suppress(OSError):
                if now - os.path.getmtime(path) > _TMP_GRACE_SECONDS:
                    os.unlink(path)
            continue
        if name.startswith(prefix) and name.endswith(".svg"):
            with contextlib.suppress(OSError):
                os.unlink(path)


def get_or_build_svg(
    workspace_id, source_sha: str, source: bytes, kind: str
) -> tuple[str | None, bytes | None]:
    """Return the cached-or-freshly-rendered SVG for a stored entry.

    Returns `(path, None)` when the SVG is on disk (a cache hit, or a fresh
    render that was cached) and `(None, bytes)` when the cache dir is
    unwritable and the SVG is only in memory — the route serves a
    `FileResponse` for the former and an in-memory `Response` for the
    latter, mirroring `preview3d.get_or_build_glb`.

    Raises the `RenderError` subclasses for the caller to map onto HTTP.
    Meant to run inside `run_in_threadpool`: the sidecar call blocks and
    prod is a single uvicorn worker.
    """
    cache_dir = _cache_dir(workspace_id)
    cache_path = os.path.join(cache_dir, _cache_name(source_sha))
    if os.path.exists(cache_path):
        return cache_path, None

    if not source:
        raise SourceEmpty("stored entry is empty")

    svg = _render_via_sidecar(kind, _payload_for(kind, source))
    if len(svg) > MAX_OUTPUT_BYTES:
        raise OutputTooLarge(f"svg is {len(svg)} bytes (cap {MAX_OUTPUT_BYTES})")

    if _write_cache(cache_dir, cache_path, svg):
        _prune(cache_dir, source_sha, os.path.basename(cache_path))
        return cache_path, None
    return None, svg
