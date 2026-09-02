"""Reading a vendor CAD archive into an importable plan.

SnapEDA, SamacSys (Component Search Engine) and UltraLibrarian all hand
out a zip holding one part's KiCad files, and each lays it out
differently. This module turns any of the three into an `ImportPlan` —
the parsed symbols, footprints, 3D models and SPICE models, plus a note
for every member we deliberately did not take. It touches no database
and no filesystem, which is what makes vendor detection and the
ambiguity rules testable without a request.

Layout detection mirrors `Steffen-W/Import-LIB-KiCad-Plugin`:

* a `KiCad/` directory  → SamacSys (3D models sit in a sibling `3D/`)
* a `KiCAD/` directory  → UltraLibrarian (capital CAD is the whole
  discriminator, so every comparison here is case-SENSITIVE)
* otherwise, a flat root carrying `.kicad_sym` / `.kicad_mod` → SnapEDA

The vendor only decides the `source` column on the rows we create;
nothing about extraction branches on it. A zip that matches none of the
shapes is still imported — we read what we recognise wherever it sits —
because the cost of guessing wrong is a mislabelled `source`, while the
cost of refusing is a user who can't import a perfectly good archive.

Everything is bounded before it is parsed: member count, declared
uncompressed total, and a per-member cap taken from
`storage.max_bytes_for`. A member that busts its cap is skipped with a
note rather than failing the archive — one oversized STEP shouldn't cost
the user the symbol they actually wanted.
"""
from __future__ import annotations

import io
import posixpath
import zipfile
from dataclasses import dataclass, replace
from dataclasses import field as dc_field
from typing import NoReturn

from fastapi import status

from app.core.errors import ErrorCodes, raise_http
from app.domain.eda import sexpr, storage

__all__ = [
    "VENDOR_SNAPEDA",
    "VENDOR_SAMACSYS",
    "VENDOR_ULTRALIBRARIAN",
    "MAX_MEMBERS",
    "MAX_UNCOMPRESSED_BYTES",
    "MAX_ENTRIES",
    "PendingEntry",
    "PendingDatafile",
    "Skipped",
    "ImportPlan",
    "read_archive",
    "read_symbol_library",
    "narrow_to_part",
]

VENDOR_SNAPEDA = "snapeda"
VENDOR_SAMACSYS = "samacsys"
VENDOR_ULTRALIBRARIAN = "ultralibrarian"

# Zip-bomb guards, checked against the central directory BEFORE a single
# member is decompressed. A real vendor archive holds well under a dozen
# files; 200 is headroom for an UltraLibrarian export that ships a whole
# `.pretty` directory.
MAX_MEMBERS = 200
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024

# Cap on the text we hand to the s-expression parser, across the whole
# archive. This is the number that bounds MEMORY, not the two above: a
# parsed node tree runs roughly 20x its source text, so 50 MiB of
# perfectly legal `.kicad_sym` members would peak around a gigabyte of
# retained objects and OOM a 1g container from a ~130 KiB upload (P3
# security review HIGH-1). 8 MiB of KiCad text is far more than any real
# vendor archive holds and peaks near 160 MiB.
MAX_PARSED_TEXT_BYTES = 8 * 1024 * 1024

# Inflate in chunks rather than asking zlib for `cap + 1` up front: a
# member that lies about its size in the central directory otherwise
# costs a full per-kind cap of decompression each, and 200 of them add up
# to gigabytes of CPU from a small upload (P3 security review MED).
_CHUNK_BYTES = 64 * 1024

# Cap on rows one import may create. Same number as MAX_MEMBERS: a
# multi-symbol library is a single member that expands to many entries,
# so the member cap alone doesn't bound the row count.
MAX_ENTRIES = 200

# Directory names that decide the vendor. Case-sensitive on purpose —
# `KiCad` and `KiCAD` are two different vendors.
_SAMACSYS_DIR = "KiCad"
_ULTRALIBRARIAN_DIR = "KiCAD"

# The pre-6.0 symbol library format. We can't read it (that needs
# kicad-cli, which we deliberately don't run at request time), so an
# archive carrying only these gets a 422 that says what to do about it.
_LEGACY_MAGIC = "EESchema-LIBRARY"

# Line prefixes that make a `.lib`/`.sub`/`.cir` look like a SPICE deck.
# A vendor `.lib` is far more often a legacy KiCad symbol library, so the
# evidence has to be positive before we treat one as a simulation model.
_SPICE_MARKERS = (
    ".subckt",
    ".model",
    ".include",
    ".param",
    ".ends",
    ".end",
    ".lib",
    ".temp",
    ".options",
)

# How much of a text member we look at to classify it. The markers we're
# after are in the first few lines of any real file.
_SNIFF_BYTES = 4096

_SYMBOL_EXT = "kicad_sym"
_FOOTPRINT_EXT = "kicad_mod"
_LEGACY_EXTS = ("lib", "dcm")

# Reasons attached to a skipped member. Stable strings — the frontend
# shows them verbatim and the tests assert on them.
SKIP_UNSUPPORTED = "unsupported file type"
SKIP_TOO_LARGE = "file exceeds the size limit for its type"
SKIP_UNREADABLE = "file could not be read"
SKIP_AMBIGUOUS_LIB = "'.lib' is neither a KiCad symbol library nor a SPICE model"
SKIP_LEGACY_SYMBOLS = "legacy KiCad 5 symbol library — convert it with kicad-cli"
SKIP_ENTRY_CAP = f"archive holds more than {MAX_ENTRIES} library entries"
SKIP_PARSE_BUDGET = "archive holds more parseable text than one import may take"
SKIP_NOT_THIS_PART = "library entry not wired to this part — import as a library to keep it"


@dataclass(frozen=True)
class PendingEntry:
    """A parsed symbol or footprint, not yet canonicalised or stored."""

    name: str
    node: sexpr.Node
    filename: str


@dataclass(frozen=True)
class PendingDatafile:
    """A validated 3D or SPICE model, stored verbatim once a row exists."""

    kind: str
    name: str
    data: bytes

    @property
    def stem(self) -> str:
        """The filename without its extension — how a footprint's
        `(model …)` path is matched back to this file."""
        return self.name.rsplit(".", 1)[0] if "." in self.name else self.name


# Member names are attacker-supplied and a zip may hold 200 of them, so
# an unclamped `filename` puts megabytes of caller-controlled text in a
# 200/422 body. Same clamp the ambiguity errors apply to entry names.
_MAX_ECHOED_NAME = 80


@dataclass(frozen=True)
class Skipped:
    filename: str
    reason: str

    def __post_init__(self) -> None:
        if len(self.filename) > _MAX_ECHOED_NAME:
            object.__setattr__(self, "filename", self.filename[:_MAX_ECHOED_NAME])


@dataclass(frozen=True)
class ImportPlan:
    vendor: str
    symbols: tuple[PendingEntry, ...] = ()
    footprints: tuple[PendingEntry, ...] = ()
    datafiles: tuple[PendingDatafile, ...] = ()
    skipped: tuple[Skipped, ...] = ()

    @property
    def entry_count(self) -> int:
        return len(self.symbols) + len(self.footprints) + len(self.datafiles)


# ---------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------


def _reject(code: str, message: str, **fields) -> NoReturn:
    raise_http(status.HTTP_422_UNPROCESSABLE_ENTITY, code=code, message=message, **fields)


# ---------------------------------------------------------------------
# Member classification
# ---------------------------------------------------------------------


def _basename(name: str) -> str:
    return posixpath.basename(name.replace("\\", "/"))


def _extension(name: str) -> str:
    base = _basename(name)
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def _is_noise(info: zipfile.ZipInfo) -> bool:
    """macOS resource forks and directory entries carry nothing we want,
    and listing them as "skipped" would be pure noise in the response."""
    name = info.filename
    return (
        info.is_dir()
        or name.startswith("__MACOSX/")
        or _basename(name).startswith("._")
        or not _basename(name)
    )


def detect_vendor(names: list[str]) -> str:
    """Which vendor produced this archive, from its directory names."""
    parts = {segment for name in names for segment in name.replace("\\", "/").split("/")[:-1]}
    if _SAMACSYS_DIR in parts:
        return VENDOR_SAMACSYS
    if _ULTRALIBRARIAN_DIR in parts:
        return VENDOR_ULTRALIBRARIAN
    return VENDOR_SNAPEDA


def _looks_like_spice(head: str) -> bool:
    for line in head.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith(_SPICE_MARKERS):
            return True
    return False


def _looks_like_legacy_symbols(head: str) -> bool:
    return head.lstrip().startswith(_LEGACY_MAGIC)


# ---------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------


def _open_archive(raw: bytes) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, OSError):
        _reject(ErrorCodes.EDA_INVALID_ARCHIVE, "not a readable zip archive")
    members = [info for info in zf.infolist() if not _is_noise(info)]
    if len(members) > MAX_MEMBERS:
        _reject(
            ErrorCodes.EDA_ARCHIVE_TOO_LARGE,
            f"archive holds {len(members)} files — the limit is {MAX_MEMBERS}",
            max_members=MAX_MEMBERS,
        )
    declared = sum(info.file_size for info in members)
    if declared > MAX_UNCOMPRESSED_BYTES:
        _reject(
            ErrorCodes.EDA_ARCHIVE_TOO_LARGE,
            (
                f"archive expands to {declared} bytes — the limit is "
                f"{MAX_UNCOMPRESSED_BYTES}"
            ),
            max_bytes=MAX_UNCOMPRESSED_BYTES,
        )
    return zf


class _Budget:
    """Running totals for one archive, so the caps bound real work.

    `inflated` is what zlib actually produced (the declared-size check in
    `_open_archive` only sees what the central directory claims);
    `parsed` is the subset we hand to the s-expression reader, which is
    what actually decides peak memory.
    """

    def __init__(self) -> None:
        self.inflated = 0
        self.parsed = 0

    def inflate(self, count: int) -> None:
        self.inflated += count
        if self.inflated > MAX_UNCOMPRESSED_BYTES:
            _reject(
                ErrorCodes.EDA_ARCHIVE_TOO_LARGE,
                (
                    f"archive expands past {MAX_UNCOMPRESSED_BYTES} bytes — "
                    "its central directory understated the real size"
                ),
                max_bytes=MAX_UNCOMPRESSED_BYTES,
            )

    def take_parse(self, count: int) -> bool:
        """Claim `count` bytes of parse budget, or report it's exhausted."""
        if self.parsed + count > MAX_PARSED_TEXT_BYTES:
            return False
        self.parsed += count
        return True


@dataclass
class _Collector:
    """What one archive walk has gathered so far."""

    budget: _Budget
    symbols: list[PendingEntry] = dc_field(default_factory=list)
    footprints: list[PendingEntry] = dc_field(default_factory=list)
    datafiles: list[PendingDatafile] = dc_field(default_factory=list)
    skipped: list[Skipped] = dc_field(default_factory=list)
    saw_legacy_symbols: bool = False
    dropped_members: int = 0

    @property
    def held(self) -> int:
        return len(self.symbols) + len(self.footprints) + len(self.datafiles)

    @property
    def room(self) -> int:
        return MAX_ENTRIES - self.held

    def skip(self, filename: str, reason: str) -> None:
        self.skipped.append(Skipped(filename=filename, reason=reason))


def _read_member(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, *, cap: int, budget: _Budget
) -> bytes | None:
    """Read one member, bounded by `cap`. None means "skip it".

    The declared size is checked first, then the read runs in chunks so a
    member that lies about its size costs us one chunk past the cap
    rather than the whole cap. Every inflated byte is charged to the
    archive-wide budget, which is what a lying central directory can't
    talk its way out of.
    """
    if info.file_size > cap:
        return None
    out = bytearray()
    try:
        with zf.open(info) as fh:
            while True:
                chunk = fh.read(_CHUNK_BYTES)
                if not chunk:
                    break
                # Charged before the cap check: these bytes were inflated
                # whether or not we end up keeping them.
                budget.inflate(len(chunk))
                out += chunk
                if len(out) > cap:
                    return None
    except (RuntimeError, zipfile.BadZipFile, OSError):
        # RuntimeError is what zipfile raises for an encrypted member.
        return None
    return bytes(out)


def read_archive(raw: bytes, *, filename: str | None = None) -> ImportPlan:
    """Parse a vendor zip into everything it offers.

    CPU-bound (parsing every symbol and footprint in the archive) — call
    it through `run_in_threadpool`, the way the P2 upload routes do.

    The entry cap is enforced INSIDE this loop, not applied to the
    finished list: trimming afterwards means every entry was parsed and
    retained first, which is the whole cost we're trying to avoid.
    """
    with _open_archive(raw) as zf:
        return _walk_archive(zf, filename=filename)


def _walk_archive(zf: zipfile.ZipFile, *, filename: str | None) -> ImportPlan:
    members = [info for info in zf.infolist() if not _is_noise(info)]
    vendor = detect_vendor([info.filename for info in members])
    col = _Collector(budget=_Budget())

    for info in members:
        name = _basename(info.filename)
        ext = _extension(info.filename)

        if col.room <= 0:
            col.dropped_members += 1
            continue

        if ext == _SYMBOL_EXT:
            _collect_symbols(zf, info, name, col)
        elif ext == _FOOTPRINT_EXT:
            _collect_footprint(zf, info, name, col)
        elif ext in ("step", "stp", "wrl", "vrml"):
            _collect_datafile(zf, info, name, col)
        elif ext in storage.SPICE_EXTENSIONS or ext in _LEGACY_EXTS:
            _collect_maybe_spice(zf, info, name, col)
        else:
            col.skip(name, SKIP_UNSUPPORTED)

    if col.dropped_members:
        col.skip(f"{col.dropped_members} files", SKIP_ENTRY_CAP)

    if col.saw_legacy_symbols and not col.symbols:
        _reject(
            ErrorCodes.EDA_LEGACY_FORMAT,
            (
                "this archive only carries a KiCad 5 (.lib) symbol library. "
                "Convert it locally first — `kicad-cli sym upgrade <file>.lib` "
                "— then import the resulting .kicad_sym."
            ),
        )

    plan = ImportPlan(
        vendor=vendor,
        symbols=tuple(col.symbols),
        footprints=tuple(col.footprints),
        datafiles=tuple(col.datafiles),
        skipped=tuple(col.skipped),
    )
    _assert_importable(plan, filename=filename)
    return plan


def read_symbol_library(raw: bytes, *, filename: str | None = None) -> ImportPlan:
    """Parse a bare multi-symbol `.kicad_sym` into a plan.

    The single-upload route refuses these and points here (its 422 says
    so); this is the endpoint that honours that promise.
    """
    # NUL-guarded: a lone NUL is valid UTF-8 and would ride all the way to
    # a Postgres DataError 500 (P3 security review HIGH-2).
    text = storage.decode_text(raw, kind="symbol library")
    try:
        found = sexpr.entries(text)
    except sexpr.SexprError as exc:
        _reject(
            ErrorCodes.EDA_INVALID_ARCHIVE,
            f"not a readable zip archive or KiCad symbol library: {exc}",
        )
    label = _basename(filename or "library.kicad_sym")
    plan = ImportPlan(
        vendor="manual",
        symbols=tuple(
            PendingEntry(name=name, node=node, filename=label)
            for name, node in found
            if name
        ),
    )
    _assert_importable(plan, filename=filename)
    return _cap_entries(plan)


def _assert_importable(plan: ImportPlan, *, filename: str | None) -> None:
    if plan.entry_count:
        return
    _reject(
        ErrorCodes.EDA_NO_ENTRIES,
        (
            "nothing importable in this archive — expected a .kicad_sym "
            "symbol, a .kicad_mod footprint, a STEP/WRL model or a SPICE model"
        ),
        skipped=[{"filename": s.filename, "reason": s.reason} for s in plan.skipped[:20]],
    )


def _cap_entries(plan: ImportPlan) -> ImportPlan:
    """Trim the plan to `MAX_ENTRIES` rows, noting what was dropped.

    Trimming rather than rejecting: an over-full file still holds the
    symbol the user came for, and the note tells them what didn't fit.

    Only `read_symbol_library` uses this. `read_archive` enforces the cap
    inside its member loop instead, because trimming after the fact means
    every entry was parsed and retained first — the exact cost the cap
    exists to avoid. Here the input is one member bounded by
    `MAX_UPLOAD_BYTES`, so post-hoc trimming is affordable.
    """
    if plan.entry_count <= MAX_ENTRIES:
        return plan
    budget = MAX_ENTRIES
    symbols = plan.symbols[:budget]
    budget -= len(symbols)
    footprints = plan.footprints[:budget]
    budget -= len(footprints)
    datafiles = plan.datafiles[:budget]
    dropped = plan.entry_count - (len(symbols) + len(footprints) + len(datafiles))
    return replace(
        plan,
        symbols=symbols,
        footprints=footprints,
        datafiles=datafiles,
        skipped=(*plan.skipped, Skipped(filename=f"{dropped} entries", reason=SKIP_ENTRY_CAP)),
    )


# ---------------------------------------------------------------------
# Per-kind collection
# ---------------------------------------------------------------------


def _collect_symbols(zf, info, name: str, col: _Collector) -> None:
    raw = _read_member(
        zf, info, cap=storage.max_bytes_for(storage.SYMBOL_KIND), budget=col.budget
    )
    if raw is None:
        col.skip(name, SKIP_TOO_LARGE)
        return
    if not col.budget.take_parse(len(raw)):
        col.skip(name, SKIP_PARSE_BUDGET)
        return
    # 422s on a NUL or non-UTF-8 member rather than skipping it: a
    # "text" file that isn't text is a corrupt archive, not one member we
    # happen not to want.
    text = storage.decode_text(raw, kind="symbol")
    try:
        found = sexpr.entries(text)
    except sexpr.SexprError:
        col.skip(name, SKIP_UNREADABLE)
        return
    # One member can hold hundreds of entries, so the cap has to bite
    # here too — not only between members.
    room = col.room
    for entry_name, node in found[:room]:
        if entry_name:
            col.symbols.append(PendingEntry(name=entry_name, node=node, filename=name))
    if len(found) > room:
        col.skip(name, SKIP_ENTRY_CAP)


def _collect_footprint(zf, info, name: str, col: _Collector) -> None:
    raw = _read_member(
        zf, info, cap=storage.max_bytes_for(storage.FOOTPRINT_KIND), budget=col.budget
    )
    if raw is None:
        col.skip(name, SKIP_TOO_LARGE)
        return
    if not col.budget.take_parse(len(raw)):
        col.skip(name, SKIP_PARSE_BUDGET)
        return
    text = storage.decode_text(raw, kind="footprint")
    try:
        node = sexpr.parse(text)
        if sexpr.head(node) not in sexpr.FOOTPRINT_ROOTS:
            raise sexpr.SexprError("not a footprint document")
        entry_name = sexpr.entry_name(node)
    except sexpr.SexprError:
        col.skip(name, SKIP_UNREADABLE)
        return
    if entry_name:
        col.footprints.append(PendingEntry(name=entry_name, node=node, filename=name))


def _collect_datafile(zf, info, name: str, col: _Collector) -> None:
    ext = _extension(name)
    kind = "step" if ext in ("step", "stp") else "wrl"
    raw = _read_member(zf, info, cap=storage.max_bytes_for(kind), budget=col.budget)
    if raw is None:
        col.skip(name, SKIP_TOO_LARGE)
        return
    leading = raw[:64].lstrip()
    expected = b"ISO-10303-21" if kind == "step" else b"#VRML"
    if not leading.startswith(expected):
        col.skip(name, SKIP_UNREADABLE)
        return
    col.datafiles.append(PendingDatafile(kind=kind, name=name, data=raw))


def _collect_maybe_spice(zf, info, name: str, col: _Collector) -> None:
    """Classify a `.lib`-family member.

    Sets `col.saw_legacy_symbols` for a KiCad 5 library, which the caller
    escalates to a 422 when the archive carries no modern symbol at all.
    """
    raw = _read_member(zf, info, cap=storage.max_bytes_for("spice"), budget=col.budget)
    if raw is None:
        col.skip(name, SKIP_TOO_LARGE)
        return
    try:
        head = raw[:_SNIFF_BYTES].decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        col.skip(name, SKIP_UNREADABLE)
        return
    if _looks_like_legacy_symbols(head):
        col.skip(name, SKIP_LEGACY_SYMBOLS)
        col.saw_legacy_symbols = True
        return
    if _extension(name) == "dcm":
        # Legacy symbol metadata — meaningless without the .lib it
        # describes, and we don't take those.
        col.skip(name, SKIP_LEGACY_SYMBOLS)
        return
    if not _looks_like_spice(head):
        # Deliberately a note, not a 422: the archive may be perfectly
        # importable apart from one file we can't place.
        col.skip(name, SKIP_AMBIGUOUS_LIB)
        return
    if b"\x00" in raw:
        col.skip(name, SKIP_UNREADABLE)
        return
    col.datafiles.append(PendingDatafile(kind="spice", name=name, data=raw))


# ---------------------------------------------------------------------
# Narrowing a library plan down to one part
# ---------------------------------------------------------------------


def _normalise(text: str) -> str:
    """Fold a name to what two vendors would agree on: case, and the
    `-`/`_`/space separators they each spell differently."""
    return "".join(c for c in text.lower() if c.isalnum())


def _stem(filename: str) -> str:
    base = _basename(filename)
    return base.rsplit(".", 1)[0] if "." in base else base


def narrow_to_part(plan: ImportPlan, *, hints: list[str]) -> ImportPlan:
    """Collapse a plan to at most one symbol and one footprint.

    A part-bound import wires exactly one of each, so an archive holding
    several is only importable if one of them is unambiguously the right
    one. `hints` are the names worth matching against — the archive's own
    filename and the part's MPN — and the footprint referenced by the
    symbol's `Footprint` property settles the footprint side.

    Anything genuinely ambiguous is a 422 listing the candidates, not a
    silent "took the first": picking wrong here means a part wired to
    someone else's footprint, which nobody would notice until the board
    came back wrong.
    """
    symbol = _pick_symbol(plan, hints=hints)
    footprint = _pick_footprint(plan, symbol=symbol, hints=hints)
    # Everything this narrowing throws away is reported, the same way a
    # member we couldn't place is — an entry that silently vanished
    # between the archive and the response is the one the user goes
    # looking for later.
    dropped = [
        Skipped(filename=entry.filename or entry.name, reason=SKIP_NOT_THIS_PART)
        for group, kept in ((plan.symbols, symbol), (plan.footprints, footprint))
        for entry in group
        if entry is not kept
    ]
    return replace(
        plan,
        symbols=(symbol,) if symbol else (),
        footprints=(footprint,) if footprint else (),
        skipped=(*plan.skipped, *dropped),
    )


def _unique_match(entries: tuple[PendingEntry, ...], wanted: set[str]) -> PendingEntry | None:
    """The single entry whose name is in `wanted`, or None if 0 or 2+."""
    wanted = {w for w in wanted if w}
    if not wanted:
        return None
    matches = [entry for entry in entries if _normalise(entry.name) in wanted]
    return matches[0] if len(matches) == 1 else None


def _pick_symbol(plan: ImportPlan, *, hints: list[str]) -> PendingEntry | None:
    """Tiers, strongest evidence first.

    The tiers are tried in order and the FIRST one that resolves wins —
    they are never unioned. Unioning lets weak evidence veto strong: a
    filename hint that happens to match a second symbol would turn an
    otherwise unambiguous archive into a 422 (P3 code review HIGH-3).
    """
    if len(plan.symbols) <= 1:
        return plan.symbols[0] if plan.symbols else None

    # 1. The symbol that names one of this archive's footprints. That is
    #    an explicit link the vendor wrote, not an inference.
    footprint_names = {_normalise(fp.name) for fp in plan.footprints}
    linked = [
        entry
        for entry in plan.symbols
        if _normalise((sexpr.get_property(entry.node, "Footprint") or "").rsplit(":", 1)[-1])
        in footprint_names - {""}
    ]
    if len(linked) == 1:
        return linked[0]

    # 2. The archive's own filename, and the part's MPN / IPN.
    match = _unique_match(plan.symbols, {_normalise(h) for h in hints if h})
    if match is not None:
        return match

    # 3. Failing both, a symbol named after a footprint in the archive.
    match = _unique_match(
        plan.symbols,
        footprint_names | {_normalise(_stem(fp.filename)) for fp in plan.footprints},
    )
    if match is not None:
        return match

    names = [entry.name[:80] for entry in plan.symbols]
    _reject(
        ErrorCodes.EDA_MULTIPLE_SYMBOLS,
        (
            f"archive holds {len(plan.symbols)} symbols and none of them "
            "clearly belongs to this part — import it as a library instead, "
            "then pick the symbol on this tab"
        ),
        symbol_count=len(plan.symbols),
        symbol_names=names[:20],
    )


def _pick_footprint(
    plan: ImportPlan, *, symbol: PendingEntry | None, hints: list[str]
) -> PendingEntry | None:
    """Same tiering as `_pick_symbol`, and for the same reason.

    The symbol's `Footprint` property is an explicit reference the vendor
    wrote down; when it names one of the archive's footprints it settles
    the question outright, and a filename hint matching a SECOND
    footprint must not be allowed to reopen it.
    """
    if len(plan.footprints) <= 1:
        return plan.footprints[0] if plan.footprints else None

    # 1. The footprint the symbol explicitly references.
    if symbol is not None:
        referenced = sexpr.get_property(symbol.node, "Footprint")
        if referenced:
            # `LibNick:Entry` — only the entry half names a footprint.
            match = _unique_match(
                plan.footprints, {_normalise(referenced.rsplit(":", 1)[-1])}
            )
            if match is not None:
                return match

    # 2. The archive's own filename, and the part's MPN / IPN.
    match = _unique_match(plan.footprints, {_normalise(h) for h in hints if h})
    if match is not None:
        return match

    names = [entry.name[:80] for entry in plan.footprints]
    _reject(
        ErrorCodes.EDA_MULTIPLE_FOOTPRINTS,
        (
            f"archive holds {len(plan.footprints)} footprints and the symbol "
            "doesn't name one of them — import it as a library instead, then "
            "pick the footprint on this tab"
        ),
        footprint_count=len(plan.footprints),
        footprint_names=names[:20],
    )
