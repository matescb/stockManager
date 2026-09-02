"""Content-addressed storage for KiCad library files.

This is a SEPARATE lane from the attachment / provider-asset store, and
deliberately so. Those two validate by magic bytes against an
allow-list of binary formats (`attachments.py::_detect_mime`,
`parts/services/assets.py::_sniff_ext`) and must not be loosened —
every format they accept is one the browser will happily render. KiCad
libraries are text, have no magic number, and are never served inline,
so they get their own validators here rather than a hole punched in
those.

Layout: `{UPLOAD_DIR}/eda/{ws_id}/{sha256}.{ext}`, one directory per
workspace, filename derived from the content. Same write idiom as
`parts/services/assets.py:294-304` — write a sibling `.tmp` then
`os.replace`, and skip entirely when the path already exists, so a
crashed write can never leave a truncated file under the canonical
name and two uploads of the same bytes cost one write.

What "validation" means per kind:

* symbol / footprint — decodes as UTF-8, has no NUL bytes, parses as an
  s-expression, and has the expected root token. The bytes we store are
  the *re-emitted* canonical form, not what was uploaded, so anything
  that survives is by construction something we can parse again.
* step / wrl — checked for their text signature. Both formats are
  ASCII-headed, which is the only cheap structural check available;
  they are stored verbatim because we never parse them.
* spice — decodes as UTF-8 with no NULs. There is no SPICE grammar
  worth enforcing here; the simulator is the arbiter.

Size caps are per-kind and enforced before any parsing, because parsing
is where the CPU goes.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import tempfile
from typing import NoReturn

from fastapi import status

from app.core.config import settings
from app.core.errors import ErrorCodes, raise_http
from app.domain.eda import sexpr

__all__ = [
    "SYMBOL_KIND",
    "FOOTPRINT_KIND",
    "DATAFILE_KINDS",
    "EXT_BY_KIND",
    "MAX_BYTES_BY_KIND",
    "SPICE_EXTENSIONS",
    "max_bytes_for",
    "datafile_kind_from_filename",
    "canonical_entry_bytes",
    "decode_text",
    "canonical_symbol",
    "canonical_footprint",
    "validated_datafile",
    "digest",
    "store",
    "path_for",
]

SYMBOL_KIND = "symbol"
FOOTPRINT_KIND = "footprint"
DATAFILE_KINDS = ("step", "wrl", "spice")

# On-disk extension per kind. SPICE files arrive under half a dozen
# extensions (.lib, .sub, .cir…) and all mean the same thing to the
# simulator, so they normalise to one.
EXT_BY_KIND: dict[str, str] = {
    SYMBOL_KIND: "kicad_sym",
    FOOTPRINT_KIND: "kicad_mod",
    "step": "step",
    "wrl": "wrl",
    "spice": "lib",
}

_MIB = 1024 * 1024

# Per-kind caps. A single symbol entry is a few KiB and the largest
# footprint in the stock KiCad libraries is well under 200 KiB, so these
# are generous. STEP and WRL fall back to the global upload cap because
# a detailed connector model genuinely runs to megabytes.
MAX_BYTES_BY_KIND: dict[str, int] = {
    SYMBOL_KIND: 1 * _MIB,
    FOOTPRINT_KIND: 2 * _MIB,
    "spice": 1 * _MIB,
}

# Recognised SPICE model extensions, mapped to the `spice` kind.
SPICE_EXTENSIONS = frozenset({"lib", "sub", "cir", "mod", "spice"})

_STEP_MAGIC = b"ISO-10303-21"
_WRL_MAGIC = b"#VRML"


def max_bytes_for(kind: str) -> int:
    """The upload cap for `kind`, defaulting to the global one."""
    return MAX_BYTES_BY_KIND.get(kind, settings().MAX_UPLOAD_BYTES)


def _reject(message: str, *, code: str = ErrorCodes.EDA_INVALID_FILE) -> NoReturn:
    raise_http(status.HTTP_422_UNPROCESSABLE_ENTITY, code=code, message=message)


def decode_text(raw: bytes, *, kind: str) -> str:
    """UTF-8 decode with a NUL-byte guard.

    The NUL check catches the common "uploaded the binary by mistake"
    case that UTF-8 alone lets through (a lone NUL is valid UTF-8), and
    keeps a stray NUL out of the Postgres `text` columns downstream —
    where it lands as a DataError 500 rather than a 422.

    Public because the zip importer decodes archive members and has to
    apply exactly this guard; a second, laxer decoder is how the NUL
    reaches Postgres (P3 security review HIGH-2).
    """
    if b"\x00" in raw:
        _reject(f"{kind} file contains NUL bytes — expected a text file")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        _reject(f"{kind} file is not valid UTF-8 text")


def datafile_kind_from_filename(filename: str | None) -> str:
    """Map an uploaded filename onto a datafile kind.

    `.step` / `.stp` → step, `.wrl` / `.vrml` → wrl, and the SPICE
    extension set → spice. Anything else is a 422 naming what we take —
    guessing from the bytes would mean sniffing three text formats that
    share no reliable discriminator.
    """
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext in ("step", "stp"):
        return "step"
    if ext in ("wrl", "vrml"):
        return "wrl"
    if ext in SPICE_EXTENSIONS:
        return "spice"
    raise_http(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        code=ErrorCodes.EDA_UNSUPPORTED_KIND,
        message=(
            "unsupported data file — expected .step/.stp, .wrl, or a SPICE "
            "model (.lib/.sub/.cir/.mod/.spice)"
        ),
    )


# Matches the String(200) `name` columns — a parsed entry name past this
# would otherwise die in Postgres as a DataError 500 (and only the
# form-supplied name was capped by the route).
_MAX_ENTRY_NAME = 200


def canonical_entry_bytes(node, *, kind: str, name: str) -> bytes:
    """Emit `node` and enforce the caps on the STORED form.

    The upload cap bounds the input, but re-emitting regenerates
    indentation — a deep-and-wide file amplified ~198x past the input
    cap before this check existed (P2 security review HIGH-1).

    Public because the zip/LCSC importers already hold a parsed node —
    the footprint's model paths are rewritten before it is stored — and
    would otherwise have to re-emit and re-parse to reach this cap.
    """
    if len(name) > _MAX_ENTRY_NAME:
        _reject(f"entry name exceeds {_MAX_ENTRY_NAME} characters")
    data = sexpr.emit(node).encode("utf-8")
    cap = max_bytes_for(kind)
    if len(data) > cap:
        _reject(
            f"canonical {kind} form exceeds {cap} bytes",
            code=ErrorCodes.EDA_FILE_TOO_LARGE,
        )
    return data


def canonical_symbol(raw: bytes) -> tuple[str, bytes]:
    """Validate a symbol upload and return `(entry_name, stored_bytes)`.

    Accepts a whole `.kicad_sym` library carrying exactly one symbol, or
    a bare `(symbol …)` entry. What gets stored is always the bare entry
    re-emitted, so every hosted symbol has the same shape regardless of
    how it arrived and phase 5 can concatenate them into a library
    without re-parsing.

    A multi-symbol library is a 422 rather than a silent "took the
    first": the file is not wrong, it just needs the zip importer that
    lands in phase 3, and the message says so.
    """
    text = decode_text(raw, kind="symbol")
    try:
        found = sexpr.entries(text)
    except sexpr.SexprError as exc:
        _reject(f"not a readable KiCad symbol file: {exc}")
    if not found:
        _reject("symbol library contains no symbols")
    if len(found) > 1:
        # Names are attacker-supplied and unbounded — truncate before
        # echoing them into the error body.
        shown = [name[:80] for name, _ in found[:5]]
        listed = ", ".join(shown) + (", …" if len(found) > len(shown) else "")
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=ErrorCodes.EDA_MULTIPLE_SYMBOLS,
            message=(
                f"file contains {len(found)} symbols ({listed}) — upload one "
                "symbol at a time, or use the library importer"
            ),
            symbol_count=len(found),
            symbol_names=[name[:80] for name, _ in found[:20]],
        )
    name, node = found[0]
    if not name:
        _reject("symbol has an empty name")
    return name, canonical_entry_bytes(node, kind=SYMBOL_KIND, name=name)


def canonical_footprint(raw: bytes) -> tuple[str, bytes]:
    """Validate a footprint upload and return `(entry_name, stored_bytes)`.

    A `.kicad_mod` holds exactly one footprint, so unlike symbols there
    is no multi-entry case to reject.
    """
    text = decode_text(raw, kind="footprint")
    try:
        node = sexpr.parse(text)
    except sexpr.SexprError as exc:
        _reject(f"not a readable KiCad footprint file: {exc}")
    root = sexpr.head(node)
    if root not in sexpr.FOOTPRINT_ROOTS:
        _reject(
            f"expected a (footprint …) document, got ({root or '?'} …)"
        )
    try:
        name = sexpr.entry_name(node)
    except sexpr.SexprError as exc:
        _reject(str(exc))
    if not name:
        _reject("footprint has an empty name")
    return name, canonical_entry_bytes(node, kind=FOOTPRINT_KIND, name=name)


def validated_datafile(kind: str, raw: bytes) -> bytes:
    """Validate a 3D or SPICE model and return the bytes to store.

    These are stored verbatim — we never parse them, so re-emitting
    would only risk corrupting something we don't understand.
    """
    # Slice before stripping: these files run to megabytes and the
    # signature is in the first line, so `raw.lstrip()` would copy the
    # whole upload to look at 12 bytes.
    leading = raw[:64].lstrip()
    if kind == "step":
        if not leading.startswith(_STEP_MAGIC):
            _reject('not a STEP file — expected it to start with "ISO-10303-21"')
        return raw
    if kind == "wrl":
        if not leading.startswith(_WRL_MAGIC):
            _reject('not a VRML file — expected it to start with "#VRML"')
        return raw
    if kind == "spice":
        decode_text(raw, kind="SPICE model")
        return raw
    raise_http(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        code=ErrorCodes.EDA_UNSUPPORTED_KIND,
        message=f"unsupported data file kind: {kind}",
    )


def _dir_for(workspace_id) -> str:
    return os.path.join(settings().UPLOAD_DIR, "eda", str(workspace_id))


def path_for(workspace_id, filename: str) -> str:
    """Absolute path of a stored file. `filename` must already be known
    flat — the serving route rejects separators before calling this."""
    return os.path.join(_dir_for(workspace_id), filename)


def digest(data: bytes) -> tuple[str, int]:
    """`(sha256, size)` of `data` without writing anything.

    Upload routes digest first and `store()` only after the row insert
    succeeds, so a rejected upload (409 name conflict, 404 category)
    leaves no orphan blob (P2 security review MEDIUM-2)."""
    return hashlib.sha256(data).hexdigest(), len(data)


def store(workspace_id, data: bytes, *, kind: str) -> tuple[str, int]:
    """Write `data` under its content hash. Returns `(sha256, size)`.

    Idempotent: identical bytes resolve to the same path, and an
    existing path is left alone. Callers dedupe at the row level off the
    returned hash.
    """
    sha = hashlib.sha256(data).hexdigest()
    ext = EXT_BY_KIND[kind]
    target_dir = _dir_for(workspace_id)
    target_path = os.path.join(target_dir, f"{sha}.{ext}")
    if not os.path.exists(target_path):
        os.makedirs(target_dir, exist_ok=True)
        # The scratch name has to be unique PER WRITER, not per target.
        # Two concurrent imports carrying the same bytes resolve to the
        # same content-addressed target, and with a shared `{sha}.tmp`
        # the first `os.replace` moved the file out from under the
        # second, which then died with FileNotFoundError. `os.replace`
        # is atomic, so both landing the same final content is fine.
        fd, tmp_path = tempfile.mkstemp(dir=target_dir, prefix=f"{sha}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp_path, target_path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise
    return sha, len(data)
