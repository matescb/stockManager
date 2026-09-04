"""STEP → GLB conversion for the CAD tab's 3D preview, with an on-disk cache.

Browsers render no STEP. KiCad shows 3D through its OCC-based viewer; the
faithful web equivalent is to tessellate the STEP into a glTF binary
(GLB) server-side, once, and let three.js draw the mesh. WRL is already a
mesh format three.js reads directly, so it never reaches this module —
only STEP does. This is the binary sibling of `preview.py`, which does
the (text, no-conversion) 2D wrapping for symbols and footprints.

The converter
-------------

`cascadio` (MIT, a thin OpenCASCADE binding shipping manylinux wheels)
does the tessellation entirely in-process — no temp files, no subprocess,
no apt OCC. It is imported lazily inside `_convert` because importing it
loads a ~28 MB shared library; deferring that to the first conversion
keeps app startup and test collection cheap.

Two facts about it drive the error handling here, both found by running
it (see `tests/test_eda_preview3d.py`):

* On unparseable input it does **not** raise — it returns empty bytes and
  writes an OCC diagnostic to the process's stderr. So "no glTF magic in
  the output" is the failure signal, alongside a defensive `except`.
* Its output is deterministic (no embedded timestamp; the only date-ish
  string is a fixed OCC version in `asset.generator`). The cache does not
  rely on that — it keys on the *source* bytes — but it means a
  regression test can compare two conversions byte-for-byte.

The cache
---------

A conversion is CPU-heavy and the source is immutable (content-addressed
by `EdaDatafile.sha256`), so the GLB is cached at

    {UPLOAD_DIR}/eda/{ws_id}/preview/{source_sha}.{CACHE_TAG}.glb

`CACHE_TAG` embeds the converter identity, so a converter bump lands on a
new filename and the stale one is pruned rather than served — the same
"a version change must invalidate" rule `pcm.py` follows for its
packages. `_prune` drops superseded tags for the *same* source and any
abandoned scratch files, but never another datafile's cache (every
datafile has its own `source_sha`, so this dir holds many live files).

Caps guard both ends: a source larger than `MAX_SOURCE_BYTES` is refused
before conversion, and a GLB larger than `MAX_OUTPUT_BYTES` is refused
after — a pathological STEP can tessellate into something far bigger than
itself.
"""
from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import time

from app.core.config import settings

__all__ = [
    "CONVERTER",
    "CONVERTER_VERSION",
    "CACHE_TAG",
    "MAX_SOURCE_BYTES",
    "MAX_OUTPUT_BYTES",
    "Preview3dError",
    "SourceMissing",
    "SourceTooLarge",
    "OutputTooLarge",
    "ConversionFailed",
    "get_or_build_glb",
]

_log = logging.getLogger(__name__)

# The converter identity. `CACHE_TAG` is what actually appears in the
# filename, so it must stay filesystem-safe (no dots that read as an
# extension). Bump `_CACHE_SCHEME` to invalidate every cached GLB without
# changing the converter — e.g. if the meshing tolerances below change.
CONVERTER = "cascadio"
CONVERTER_VERSION = "0.1.1"
_CACHE_SCHEME = "v1"
CACHE_TAG = f"{_CACHE_SCHEME}-{CONVERTER}{CONVERTER_VERSION.replace('.', '_')}"

_MIB = 1024 * 1024
# Uploads already cap STEP at the global MAX_UPLOAD_BYTES (10 MiB), so this
# is a belt-and-braces ceiling for anything that reaches the store by
# another lane (the zip importer). A STEP past this is refused unconverted.
MAX_SOURCE_BYTES = 25 * _MIB
# Tessellation can amplify: a source with many high-curvature faces meshes
# into far more triangles than its byte size suggests. Refuse a GLB this
# large rather than stream tens of MiB of mesh to a browser tab.
MAX_OUTPUT_BYTES = 50 * _MIB

# Meshing tolerances handed to cascadio. Coarser than a manufacturing mesh
# on purpose — this is a preview, and a tighter deflection multiplies the
# triangle count (and the GLB size) for detail a thumbnail-sized viewer
# cannot show. If you change these, bump `_CACHE_SCHEME`.
_TOL_LINEAR = 0.05
_TOL_ANGULAR = 0.5


class Preview3dError(Exception):
    """Base for the failures the route maps onto HTTP status codes."""


class SourceMissing(Preview3dError):
    """The content-addressed source blob is gone — a 'can't happen'."""


class SourceTooLarge(Preview3dError):
    """Source exceeds `MAX_SOURCE_BYTES`; refused before conversion."""


class OutputTooLarge(Preview3dError):
    """Converted GLB exceeds `MAX_OUTPUT_BYTES`; refused after conversion."""


class ConversionFailed(Preview3dError):
    """The converter could not turn the source into a glTF binary."""


def _cache_dir(workspace_id) -> str:
    return os.path.join(settings().UPLOAD_DIR, "eda", str(workspace_id), "preview")


def _cache_name(source_sha: str) -> str:
    return f"{source_sha}.{CACHE_TAG}.glb"


def _convert(source: bytes) -> bytes:
    """Tessellate STEP `source` into GLB bytes, or raise `ConversionFailed`.

    Lazily imports cascadio (loads a ~28 MB OCC library on first call).
    Handles both failure modes: an exception, and the empty-bytes-plus-
    stderr-noise that cascadio returns on malformed input.
    """
    import cascadio
    from cascadio._core import FileType

    try:
        glb = cascadio.to_glb_bytes(
            source,
            FileType.STEP,
            tol_linear=_TOL_LINEAR,
            tol_angular=_TOL_ANGULAR,
        )
    except Exception as exc:
        # Any converter fault is a 422, not a 500 — the spec is explicit that
        # a bad STEP must never surface as a server error. cascadio usually
        # returns empty bytes (handled below) rather than raising, but some
        # inputs do throw, so this is the belt to that braces.
        raise ConversionFailed(f"converter raised {type(exc).__name__}") from exc
    # A valid GLB starts with the glTF magic; empty bytes (or anything
    # else) means the STEP did not parse.
    if not glb or glb[:4] != b"glTF":
        raise ConversionFailed("converter produced no glTF output")
    return glb


# How long a scratch file may sit before it is treated as debris — long
# enough that an in-flight conversion is never touched, short enough that
# a process killed mid-write doesn't leave one behind forever. Mirrors
# `pcm.py::_TMP_GRACE_SECONDS`.
_TMP_GRACE_SECONDS = 3600
_SCRATCH_SUFFIX = ".glb.tmp"


def _write_cache(cache_dir: str, cache_path: str, data: bytes) -> bool:
    """Write `data` to `cache_path` atomically. False if the dir is unusable.

    Same idiom as `storage.store` / `pcm._build_to_cache`: a uniquely
    named scratch file then `os.replace`, so a reader never sees a partial
    GLB and a crashed write leaves only a prunable `.tmp`.
    """
    try:
        os.makedirs(cache_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=cache_dir, suffix=_SCRATCH_SUFFIX)
    except OSError:
        _log.warning("eda 3d preview: cache dir unusable (%s)", cache_dir)
        return False
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_path, cache_path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        _log.warning("eda 3d preview: could not write cache file (%s)", cache_path)
        return False
    return True


def _prune(cache_dir: str, source_sha: str, keep: str) -> None:
    """Drop superseded-tag GLBs for `source_sha` and stale scratch files.

    Scoped to names this module writes. A `{source_sha}.*.glb` that is not
    the current tag is a leftover from an older converter and is removed;
    every *other* datafile's cache has a different `source_sha` prefix and
    is left untouched. Abandoned `.tmp` files are pruned by age.
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
        if name.startswith(prefix) and name.endswith(".glb"):
            with contextlib.suppress(OSError):
                os.unlink(path)


def get_or_build_glb(
    workspace_id, source_sha: str, source_path: str
) -> tuple[str | None, bytes | None]:
    """Return the cached-or-freshly-built GLB for a STEP source.

    Returns `(path, None)` when the GLB is on disk (a cache hit, or a
    fresh build that was cached) and `(None, bytes)` when the cache dir is
    unwritable and the GLB is only in memory — the route serves a
    `FileResponse` for the former and an in-memory `Response` for the
    latter, mirroring `pcm.py`'s cache/in-memory split.

    Raises the `Preview3dError` subclasses for the caller to map onto 4xx.
    All of this — the file reads and the CPU-heavy conversion — is meant
    to run inside `run_in_threadpool`; prod is a single uvicorn worker.
    """
    cache_dir = _cache_dir(workspace_id)
    cache_path = os.path.join(cache_dir, _cache_name(source_sha))
    if os.path.exists(cache_path):
        return cache_path, None

    try:
        size = os.path.getsize(source_path)
    except OSError as exc:
        raise SourceMissing(str(exc)) from exc
    if size > MAX_SOURCE_BYTES:
        raise SourceTooLarge(f"source is {size} bytes (cap {MAX_SOURCE_BYTES})")

    try:
        with open(source_path, "rb") as handle:
            source = handle.read()
    except OSError as exc:
        raise SourceMissing(str(exc)) from exc

    glb = _convert(source)
    if len(glb) > MAX_OUTPUT_BYTES:
        raise OutputTooLarge(f"glb is {len(glb)} bytes (cap {MAX_OUTPUT_BYTES})")

    if _write_cache(cache_dir, cache_path, glb):
        _prune(cache_dir, source_sha, os.path.basename(cache_path))
        return cache_path, None
    return None, glb
