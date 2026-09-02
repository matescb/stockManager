"""The PCM package a workspace's KiCad libraries are shipped as.

`api/routes/kicad_pcm.py` serves three documents — `repository.json`,
`packages.json` and the package zip — and everything they contain is
built here. The naming half of the contract (file stems, library
nicknames, the install paths a `(model …)` node has to name) lives in
`kicad_refs.py`; this module is the packaging half.

One package per workspace
-------------------------

Not one per category. The PCM's unit of installation is the package, so
per-category packages would mean a user clicking Install once per
category and again whenever a new one appeared. Categories survive as
the *libraries inside* the package — one `SM_<slug>.kicad_sym` and one
`SM_<slug>.pretty/` each, which is what KiCad registers under
`PCM_SM_<slug>` and what phase 5's `symbolIdStr` values name.

Versioning is stateless
-----------------------

The PCM decides an update is available by comparing version strings, so
the version has to grow whenever the content changes — without a table
to remember what we last shipped. `_derive_version` reads it off the
newest `updated_at` in the workspace: `<major>.<days>.<half-seconds>`
since 2026-01-01, which is monotonic by construction because clocks are.
Two changes inside the same two-second tick collapse to one version;
they do *not* collapse to one package, because the cache is keyed on a
content fingerprint rather than on the version (see `build_package`), so
the second change still ships — it just doesn't prompt an already
up-to-date user to update again.

`eda_footprint_models` carries no timestamps, so linking a 3D model to a
footprint is made to bump the FOOTPRINT's `updated_at` instead
(`service.py::link_footprint_model`). Without that, attaching a model
would change the package's content without advancing its version.

What ships
----------

Active rows only, exactly as phase 5 resolves them: an entry whose own
category is archived is packaged under `SM_uncategorized`, because that
is the nickname phase 5 already told KiCad to expect.

Everything is read from the content-addressed store in `storage.py`.
Symbol libraries are a concatenation of stored canonical `(symbol …)`
entries inside a `(kicad_symbol_lib …)` wrapper — no re-parse, because
what is stored is already canonical. Footprints ARE re-parsed, for one
reason: their stored `${STOCKMGR_3D}/<name>` model paths have to become
the install path the PCM will actually extract to.

Determinism
-----------

Two builds of unchanged content produce byte-identical zips: members are
emitted in a fixed order with a fixed timestamp and a fixed
`create_system`. That is what makes `download_sha256` stable, and it is
what lets the on-disk cache be content-addressed.
"""
from __future__ import annotations

import contextlib
import hashlib
import itertools
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, NoReturn
from uuid import UUID

from fastapi import status
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ErrorCodes, raise_http
from app.domain.categories.models import PartCategory
from app.domain.eda import kicad_library, kicad_refs, sexpr, storage
from app.domain.eda.models import EdaDatafile, EdaFootprint, EdaSymbol

__all__ = [
    "PCM_PREFIX",
    "READ_ONLY_TOKEN_PLACEHOLDER",
    "REPOSITORY_DOCUMENT",
    "PACKAGES_DOCUMENT",
    "PACKAGE_ARCHIVE",
    "document_url",
    "MAX_CONTENT_BYTES",
    "PACKAGE_TYPE",
    "PACKAGE_LICENSE",
    "KICAD_VERSION",
    "VERSION_EPOCH",
    "BuiltPackage",
    "Plan",
    "plan_package",
    "write_archive",
    "materialise",
    "build_package",
    "metadata_document",
    "packages_document",
    "repository_document",
    "json_bytes",
]

_log = logging.getLogger(__name__)

# Where `api/routes/kicad_pcm.py` answers, hung off the phase-5 mount so
# there is one KiCad prefix in nginx and one in the routing table.
PCM_PREFIX = f"{kicad_library.API_PREFIX}/pcm"

REPOSITORY_DOCUMENT = "repository.json"
PACKAGES_DOCUMENT = "packages.json"
PACKAGE_ARCHIVE = "package.zip"

# Stands in for the token in the URL `GET /api/eda/kicad-setup` hands the
# settings UI. Distinct from `kicad_library.TOKEN_PLACEHOLDER` because
# this one has an extra requirement attached: a full-parity token in a
# URL is refused here, so the placeholder has to say so.
READ_ONLY_TOKEN_PLACEHOLDER = "PASTE_YOUR_READONLY_TOKEN"


def document_url(token: str, document: str) -> str:
    """Absolute URL of one PCM document.

    Built from `APP_BASE_URL` rather than `request.base_url`, matching
    `kicad_library._base_url`. The Host header is client-supplied even
    behind our proxy, and these URLs are written into a document the PCM
    will fetch later — an attacker-chosen host would redirect a client's
    next request, token and all, wherever they liked.
    """
    base = settings().APP_BASE_URL.rstrip("/")
    return f"{base}{PCM_PREFIX}/{token}/{document}"


# v1 of the PCM schema calls this out as one of three types; v2 accepts
# any lowercase string but KiCad still keys its library handling off it.
PACKAGE_TYPE = "library"

# MUST be a member of the 90-value enum in KiCad's v1 schema
# (`tests/fixtures/pcm.v1.schema.json`, `#/definitions/License`).
# `license` is only a free-form string in the v2 schema; v1 closes it, and
# `PLUGIN_CONTENT_MANAGER::ValidateJson` rejects the WHOLE document over
# one bad value — so `proprietary`, which reads as the obvious label here
# and passes v2, silently stopped the repository from loading at all.
# `unrestricted` is the enum's catch-all for "no standard licence
# applies", which is the honest description of a private workspace's own
# library. `tests/test_kicad_pcm.py` validates the served bytes against
# the vendored schema so this can't regress.
PACKAGE_LICENSE = "unrestricted"

# The floor, not the target: `kicad_version` is the MINIMUM version a
# package supports. 8.0 is the oldest release whose PCM understands
# everything we emit, and it is the major `kicad_refs.THIRD_PARTY_VAR`
# is pinned to.
KICAD_VERSION = "8.0"

# `(version …)` in the generated symbol libraries. KiCad writes a date
# stamp here for its own format revisions; 20211014 is the 6.0 baseline
# and every later release still reads it.
SYMBOL_LIB_FORMAT_VERSION = "20211014"
GENERATOR = "stockmanager"

# Refuse to build past this much source content. A build reads every
# stored file into memory and deflates it inside a request, so the cap is
# what stops one enormous workspace from pinning a worker and the
# 60-second proxy timeout from firing mid-stream. Typical workspaces are
# three orders of magnitude under it.
MAX_CONTENT_BYTES = 200 * 1024 * 1024

# Version-number origin. Absolute rather than relative so the derived
# version can't move backwards when the code is redeployed.
VERSION_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)

# The PCM schema gives the minor field four digits, so days roll into the
# major every 10000 of them (2053-ish). Rolling rather than clamping is
# what keeps the sequence monotonic past that date.
_MINOR_ROLLOVER = 10_000

# Patch resolution. Seconds-in-day is 86399 at most, which needs six
# digits; the schema allows six, but halving leaves headroom and costs
# only the ability to tell two edits two seconds apart apart.
_VERSION_TICK_SECONDS = 2

# Fixed member timestamp. Zip stores local time with no zone, so the
# build host's clock would otherwise leak into `download_sha256` and
# break the "unchanged content, identical bytes" guarantee. 1980-01-01 is
# the earliest a zip can represent.
_ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)

# 3 = Unix. `ZipInfo` picks this from the build platform otherwise, which
# is the same determinism problem as the timestamp.
_ZIP_CREATE_SYSTEM = 3
_ZIP_FILE_MODE = 0o644 << 16

_METADATA_MEMBER = "metadata.json"

# Scratch-file naming. Shared by the zip and its sidecar so `_prune` has
# exactly one pattern to recognise as debris.
_SCRATCH_PREFIX = "pcm."
_SCRATCH_SUFFIX = ".tmp"

# Chunk size for streaming copies and digests. Big enough that the
# syscall overhead is noise, small enough that it is not the thing this
# module is trying to avoid holding.
_COPY_CHUNK_BYTES = 1024 * 1024

# Concurrent builds allowed process-wide. Small on purpose: see
# `materialise`. uvicorn runs one worker (ADR-0012), so this genuinely is
# the process-wide cap and not a per-thread illusion.
_BUILD_SLOTS = threading.BoundedSemaphore(2)

# How long a request waits for a build slot before giving up. Comfortably
# inside nginx's 60-second `proxy_read_timeout`, so a caller gets our 503
# rather than a truncated response.
_BUILD_WAIT_SECONDS = 30

# Characters that must not appear in an archive member name. The first
# group would let a name escape its directory (`..`, `a/../b`) or name a
# different one; the rest are illegal in Windows filenames, and KiCad
# runs there. Entry names reach us from parsed file content and from an
# unconstrained `String(200)` form field, so neither is trustworthy.
_UNSAFE_NAME_CHARS = frozenset('/\\:*?"<>|')

# The stored form of a 3D reference, which the build rewrites away.
_STORED_MODEL_PREFIX = f"{kicad_refs.MODEL_PATH_VAR}/"

# Tie-break when two data files want the same archive member. Uniqueness
# on `eda_datafiles` is (workspace, KIND, name), so a STEP and a WRL may
# legitimately share a name — but both land in `3dmodels/<name>`, and a
# zip with two members at one path extracts unpredictably. STEP wins:
# it's the format this repo prefers wherever both exist, and the
# `(model …)` path inside the footprint names the row's `name` with no
# way to say which kind it meant, so the reference resolves either way.
_MODEL_KIND_RANK = {"step": 0, "wrl": 1}

# Schema caps on the three description fields.
_MAX_NAME = 200
_MAX_DESCRIPTION = 500
_MAX_DESCRIPTION_FULL = 5000


def _unavailable(message: str) -> NoReturn:
    """The one non-404 this surface raises.

    Reaching it needs a valid read-only token, so unlike the 404 it is
    not an oracle for anything — it means the package genuinely could not
    be produced, and KiCad reports "unavailable" either way.
    """
    raise_http(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        ErrorCodes.KICAD_PACKAGE_UNAVAILABLE,
        message,
    )


# ---------------------------------------------------------------------
# Plan — everything the build needs, resolved from the database
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class _StoredEntry:
    """One stored blob, and where it goes.

    `stem` is empty for entries that aren't filed into a per-category
    library (3D models, SPICE); `member` is empty for symbols, whose
    bytes are merged into a library file rather than copied.
    """

    name: str
    sha256: str
    size_bytes: int
    ext: str
    stem: str = ""
    member: str = ""
    kind: str = ""


@dataclass(frozen=True)
class Plan:
    """The package's contents, before any file has been opened.

    Built entirely from the database so the size cap can be enforced —
    and a cache hit answered — without touching the filesystem.
    """

    workspace_id: UUID
    workspace_name: str
    identifier: str
    version: str
    update_timestamp: int
    symbols: tuple[_StoredEntry, ...]
    footprints: tuple[_StoredEntry, ...]
    blobs: tuple[_StoredEntry, ...]
    fingerprint: str

    @property
    def is_empty(self) -> bool:
        """No active library content at all.

        A workspace like this still gets a valid repository — with zero
        packages in it, because there is nothing to install.
        """
        return not (self.symbols or self.footprints or self.blobs)


@dataclass(frozen=True)
class BuiltPackage:
    """A rendered package, plus the numbers `packages.json` has to quote.

    The bytes are described but not necessarily held. `path` is the
    normal case — the archive is on disk and the zip route streams it,
    which is what keeps a 200 MiB package from being a 200 MiB resident
    buffer per concurrent request. `data` is the fallback for a cache
    directory we could not write to.

    Exactly one of them is set; `serve_from_disk` says which.
    """

    plan: Plan
    sha256: str
    download_size: int
    install_size: int
    path: str | None = None
    data: bytes | None = None

    @property
    def serve_from_disk(self) -> bool:
        return self.path is not None

    @property
    def version(self) -> str:
        return self.plan.version

    @property
    def identifier(self) -> str:
        return self.plan.identifier


def _is_safe_member_name(name: str) -> bool:
    """Whether `name` is usable as a flat filename inside the archive."""
    if not name or name in (".", ".."):
        return False
    if name[-1] in " .":
        # Trailing dots and spaces are silently stripped by Windows,
        # which would make two distinct entries collide on extraction.
        return False
    return not any(ch in _UNSAFE_NAME_CHARS or ch < " " for ch in name)


def _derive_version(latest: datetime | None) -> tuple[str, int]:
    """`(version, update_timestamp)` for a workspace last changed at `latest`.

    Clamped to `VERSION_EPOCH` at the bottom: a row older than the epoch
    (a restored backup, a skewed clock) would otherwise produce a
    negative component, which the PCM's version pattern rejects outright.
    """
    moment = latest or VERSION_EPOCH
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if moment < VERSION_EPOCH:
        moment = VERSION_EPOCH
    delta = moment - VERSION_EPOCH
    version = (
        f"{1 + delta.days // _MINOR_ROLLOVER}."
        f"{delta.days % _MINOR_ROLLOVER}."
        f"{delta.seconds // _VERSION_TICK_SECONDS}"
    )
    return version, int(moment.timestamp())


# Every table whose content the package is built from and that carries a
# timestamp. `eda_footprint_models` is absent because it has no columns
# to read — see the module docstring.
_TIMESTAMPED = (EdaSymbol, EdaFootprint, EdaDatafile, PartCategory)


def _latest_change(db: Session, *, workspace_id: UUID) -> datetime | None:
    """The newest `updated_at` across everything the package depends on.

    Archived rows count. Archiving an entry REMOVES it from the package,
    which is a content change like any other; skipping archived rows
    would let the version go backwards the moment the newest row was the
    one just archived.

    KNOWN GAP — renaming the workspace. The package's `name` and
    descriptions are built from `Workspace.name`, but `workspaces` has no
    `updated_at` column (only `created_at`), so there is nothing here to
    read and a rename does not move the version or `update_timestamp`.
    Consequences, in order of how much they matter:

    * The served documents and the archive still AGREE, because the
      workspace name is part of the cache fingerprint — the rename
      rebuilds the zip. Nothing is internally inconsistent.
    * KiCad will not notice the new name until the next real content
      change, because `CacheRepository` re-fetches on a moved
      `update_timestamp` and nothing else. A cosmetic staleness on an
      already-installed package.

    Closing it properly needs an `updated_at` on `workspaces`, i.e. a
    migration; that is deliberately not in this phase's scope. Do not
    reach for the audit log instead — its workspace actions are split
    across several names (`workspace.credentials_rotated`,
    `workspace.active_lists_updated`, …) and audit rows are prunable, so
    package versioning would inherit both a naming coupling and a
    retention policy.
    """
    stamps = [
        db.execute(
            select(func.max(Model.updated_at)).where(Model.workspace_id == workspace_id)
        ).scalar()
        for Model in _TIMESTAMPED
    ]
    return max((stamp for stamp in stamps if stamp is not None), default=None)


def _library_entries(db: Session, Model, *, workspace_id: UUID) -> list[tuple[str, str, int, str]]:
    """Active symbols or footprints as `(name, sha256, size, stem)`.

    The category join carries `archived_at IS NULL` and a `workspace_id`
    equality check of its own — the first because an entry under an
    archived category files under `SM_uncategorized` (phase 5 already
    told KiCad so), the second because isolation here is enforced in
    code, never inferred from an FK (ADR-0002).
    """
    rows = db.execute(
        select(Model.name, Model.sha256, Model.size_bytes, PartCategory.library_slug)
        .outerjoin(
            PartCategory,
            and_(
                PartCategory.id == Model.category_id,
                PartCategory.workspace_id == Model.workspace_id,
                PartCategory.archived_at.is_(None),
            ),
        )
        .where(Model.workspace_id == workspace_id)
        .where(Model.archived_at.is_(None))
    ).all()
    return [
        (name, sha, size, kicad_refs.package_stem(slug)) for name, sha, size, slug in rows
    ]


def _datafiles(db: Session, *, workspace_id: UUID) -> list[tuple[str, str, str, int]]:
    """Active data files as `(kind, name, sha256, size)`."""
    return [
        (kind, name, sha, size)
        for kind, name, sha, size in db.execute(
            select(
                EdaDatafile.kind,
                EdaDatafile.name,
                EdaDatafile.sha256,
                EdaDatafile.size_bytes,
            )
            .where(EdaDatafile.workspace_id == workspace_id)
            .where(EdaDatafile.archived_at.is_(None))
        ).all()
    ]


def plan_package(db: Session, *, ws) -> Plan:
    """Resolve what the workspace's package contains.

    Entries whose name isn't a usable flat filename are dropped rather
    than sanitised: a rewritten filename would no longer match the
    `LibNick:Entry` reference phase 5 hands KiCad, so the entry would be
    broken either way — and emitting the name verbatim would put a
    path-traversing member in an archive we hand to a desktop
    application.
    """
    workspace_id = ws.id
    version, update_timestamp = _derive_version(
        _latest_change(db, workspace_id=workspace_id)
    )
    identifier = kicad_refs.package_identifier(workspace_id)

    skipped = 0
    symbols: list[_StoredEntry] = []
    for name, sha, size, stem in _library_entries(db, EdaSymbol, workspace_id=workspace_id):
        # `stem` is checked as well as `name` because for a symbol the STEM
        # is the member name (`symbols/<stem>.kicad_sym`) — the entry name
        # only ever goes inside the file. Slugs are pattern-validated at
        # the category, so this can't fire today; it is here because the
        # footprint branch below needs the identical pair of checks and a
        # rule that holds on one branch and not the other is the kind that
        # rots.
        if not _is_safe_member_name(name) or not _is_safe_member_name(stem):
            skipped += 1
            continue
        symbols.append(
            _StoredEntry(
                name=name,
                sha256=sha,
                size_bytes=size,
                ext=storage.EXT_BY_KIND[storage.SYMBOL_KIND],
                stem=stem,
            )
        )

    footprints: list[_StoredEntry] = []
    for name, sha, size, stem in _library_entries(
        db, EdaFootprint, workspace_id=workspace_id
    ):
        if not _is_safe_member_name(name) or not _is_safe_member_name(stem):
            skipped += 1
            continue
        ext = storage.EXT_BY_KIND[storage.FOOTPRINT_KIND]
        footprints.append(
            _StoredEntry(
                name=name,
                sha256=sha,
                size_bytes=size,
                ext=ext,
                stem=stem,
                member=(
                    f"{kicad_refs.FOOTPRINTS_DIR}/{stem}{kicad_refs.PRETTY_SUFFIX}"
                    f"/{name}.{ext}"
                ),
            )
        )

    blobs: list[_StoredEntry] = []
    for kind, name, sha, size in _datafiles(db, workspace_id=workspace_id):
        if not _is_safe_member_name(name):
            skipped += 1
            continue
        if kind == "spice":
            member = f"{kicad_refs.RESOURCES_DIR}/{kicad_refs.SPICE_SUBDIR}/{name}"
        else:
            member = f"{kicad_refs.MODELS_DIR}/{name}"
        blobs.append(
            _StoredEntry(
                name=name,
                sha256=sha,
                size_bytes=size,
                ext=storage.EXT_BY_KIND[kind],
                member=member,
                kind=kind,
            )
        )

    if skipped:
        # Count only. The names are the reason they were skipped, so
        # they're exactly the strings not to paste into a log line.
        _log.warning(
            "pcm: skipped %d entry name(s) unusable as filenames in workspace %s",
            skipped,
            workspace_id,
        )

    symbols.sort(key=lambda entry: (entry.stem, entry.name))
    footprints.sort(key=lambda entry: (entry.stem, entry.name))
    blobs.sort(
        key=lambda entry: (entry.member, _MODEL_KIND_RANK.get(entry.kind, 9), entry.name)
    )
    blobs = _dedupe_members(blobs, workspace_id=workspace_id)

    total = sum(entry.size_bytes for entry in (*symbols, *footprints, *blobs))
    if total > MAX_CONTENT_BYTES:
        _unavailable(
            f"library content is {total} bytes, over the "
            f"{MAX_CONTENT_BYTES}-byte packaging limit"
        )

    return Plan(
        workspace_id=workspace_id,
        workspace_name=ws.name or "",
        identifier=identifier,
        version=version,
        update_timestamp=update_timestamp,
        symbols=tuple(symbols),
        footprints=tuple(footprints),
        blobs=tuple(blobs),
        fingerprint=_fingerprint(
            identifier, version, ws.name or "", symbols, footprints, blobs
        ),
    )


def _dedupe_members(
    entries: list[_StoredEntry], *, workspace_id: UUID
) -> list[_StoredEntry]:
    """Keep one entry per archive member, first wins.

    Callers must sort first — the caller's sort is what decides which of a
    colliding pair survives (see `_MODEL_KIND_RANK`). A zip may physically
    hold two members at one path, but which one lands on disk is up to the
    extractor, so a duplicate is a coin toss rather than an error.
    """
    winners: dict[str, _StoredEntry] = {}
    for entry in entries:
        winner = winners.setdefault(entry.member, entry)
        if winner is not entry:
            _log.warning(
                "pcm: %s is claimed by more than one data file in workspace %s — "
                "shipping the %s, dropping the %s",
                entry.member,
                workspace_id,
                winner.kind or "first",
                entry.kind or "other",
            )
    return [entry for entry in entries if winners[entry.member] is entry]


def _fingerprint(
    identifier: str,
    version: str,
    workspace_name: str,
    symbols: list[_StoredEntry],
    footprints: list[_StoredEntry],
    blobs: list[_StoredEntry],
) -> str:
    """A digest of everything that can change the rendered bytes.

    The cache key. Deliberately NOT the version: two edits inside one
    two-second tick share a version but not a fingerprint, and keying the
    cache on the version would serve the first edit's zip forever.

    `workspace_name` is in here because the package's `name`,
    `description` and `description_full` are built from it — and it lives
    in `metadata.json` INSIDE the zip as well as in `packages.json`. Leave
    it out and a rename serves a freshly-computed `packages.json` beside a
    cached archive whose metadata still says the old name, which is the
    two disagreeing about the same package. See `_latest_change` for the
    part of this that a fingerprint cannot fix.
    """
    hasher = hashlib.sha256()
    hasher.update(f"{identifier}\n{version}\n{workspace_name}\n".encode())
    for label, entries in (("sym", symbols), ("fp", footprints), ("blob", blobs)):
        for entry in entries:
            hasher.update(
                f"{label}\t{entry.stem}\t{entry.name}\t{entry.sha256}\n".encode()
            )
    return hasher.hexdigest()


# ---------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------


def _read_stored(workspace_id: UUID, entry: _StoredEntry) -> bytes:
    path = storage.path_for(workspace_id, f"{entry.sha256}.{entry.ext}")
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        # The store is content-addressed and append-only, so this is a
        # "can't happen" — which is exactly why it must be loud rather
        # than quietly shipping a library with a hole in it.
        _log.error("pcm: stored file missing for %s.%s", entry.sha256, entry.ext)
        _unavailable("a library file is missing from storage")


def _symbol_library(entry_bytes: list[bytes]) -> bytes:
    """Wrap stored canonical `(symbol …)` entries into one library file.

    A plain concatenation — the stored form is already the canonical
    emission of a single entry, so there is nothing to re-parse and no
    opportunity for the round-trip to change what KiCad reads.
    """
    header = (
        f"(kicad_symbol_lib (version {SYMBOL_LIB_FORMAT_VERSION}) "
        f"(generator {GENERATOR})\n"
    ).encode()
    return header + b"\n".join(entry_bytes) + b"\n)\n"


def _footprint_for_package(identifier: str, raw: bytes) -> bytes:
    """Re-point a stored footprint's 3D models at their install location.

    Paths that aren't ours are left exactly as they are: a hand-uploaded
    footprint may legitimately reference `${KICAD8_3DMODEL_DIR}` or some
    other library the user already has.

    A `${STOCKMGR_3D}/…` remainder is checked before it is substituted.
    It is not a zip member, so the archive-member guard never sees it —
    but it is interpolated into an absolute path that KiCad resolves ON
    THE USER'S MACHINE, so a stored `../../..` would walk out of the
    installed package's directory there. The node is dropped rather than
    rewritten, which is what the phase-3 importer already does for a
    model whose file wasn't in the archive.
    """

    def rewrite(path: str) -> str | None:
        if not path.startswith(_STORED_MODEL_PREFIX):
            return path
        name = path[len(_STORED_MODEL_PREFIX):]
        if not _is_safe_member_name(name):
            _log.warning("pcm: dropped a 3D model reference that is not a filename")
            return None
        return kicad_refs.pcm_model_path(identifier, name)

    try:
        node = sexpr.parse(raw.decode("utf-8"))
    except (UnicodeDecodeError, sexpr.SexprError):
        _log.error("pcm: stored footprint is not parseable")
        _unavailable("a stored footprint could not be read")
    return sexpr.emit(sexpr.rewrite_model_paths(node, rewrite)).encode("utf-8")


def _member_info(member: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(member, date_time=_ZIP_DATE_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = _ZIP_CREATE_SYSTEM
    info.external_attr = _ZIP_FILE_MODE
    return info


def _add_member(archive: zipfile.ZipFile, member: str, data: bytes) -> None:
    archive.writestr(_member_info(member), data)


def _write_symbol_library(
    archive: zipfile.ZipFile, workspace_id: UUID, stem: str, entries: list[_StoredEntry]
) -> int:
    """Stream one category's symbols into a `.kicad_sym` library.

    A plain concatenation of stored canonical `(symbol …)` entries inside
    a `(kicad_symbol_lib …)` wrapper: the stored form is already the
    canonical emission of a single entry, so there is nothing to re-parse
    and no chance for a round-trip to change what KiCad reads.

    Written entry-by-entry into the open member rather than joined first,
    so peak memory is ONE symbol rather than the whole library plus the
    joined copy of it.
    """
    header = (
        f"(kicad_symbol_lib (version {SYMBOL_LIB_FORMAT_VERSION}) "
        f"(generator {GENERATOR})\n"
    ).encode()
    ext = storage.EXT_BY_KIND[storage.SYMBOL_KIND]
    written = 0
    with archive.open(
        _member_info(f"{kicad_refs.SYMBOLS_DIR}/{stem}.{ext}"), "w"
    ) as sink:
        sink.write(header)
        written += len(header)
        for index, entry in enumerate(entries):
            if index:
                sink.write(b"\n")
                written += 1
            data = _read_stored(workspace_id, entry)
            sink.write(data)
            written += len(data)
        sink.write(b"\n)\n")
        written += 3
    return written


def _copy_stored(
    archive: zipfile.ZipFile, workspace_id: UUID, entry: _StoredEntry
) -> int:
    """Copy a stored blob into the archive without holding it in memory.

    This is where the big files are — a detailed STEP model runs to
    megabytes, and `MAX_CONTENT_BYTES` allows 200 MiB of them. Reading one
    into a `bytes` only to hand it to `writestr` doubles that peak for no
    reason; `shutil.copyfileobj` moves it a chunk at a time instead.
    """
    path = storage.path_for(workspace_id, f"{entry.sha256}.{entry.ext}")
    try:
        with open(path, "rb") as source:
            with archive.open(_member_info(entry.member), "w") as sink:
                shutil.copyfileobj(source, sink, _COPY_CHUNK_BYTES)
    except OSError:
        _log.error("pcm: stored file missing for %s.%s", entry.sha256, entry.ext)
        _unavailable("a library file is missing from storage")
    return entry.size_bytes


def write_archive(plan: Plan, sink) -> int:
    """Write the package into `sink`, returning its uncompressed size.

    `sink` is any binary file object — a scratch file on the way to the
    cache, or a `BytesIO` when the cache is unwritable. One code path for
    both, so the archive cannot differ between them.

    Reads files, touches no database.
    """
    install_size = 0

    with zipfile.ZipFile(sink, "w") as archive:
        metadata = json_bytes(metadata_document(plan))
        _add_member(archive, _METADATA_MEMBER, metadata)
        install_size += len(metadata)

        # `plan.symbols` is sorted by (stem, name), so consecutive runs of
        # one stem are exactly one library's contents, in a stable order.
        for stem, entries in itertools.groupby(plan.symbols, key=lambda e: e.stem):
            install_size += _write_symbol_library(
                archive, plan.workspace_id, stem, list(entries)
            )

        for entry in plan.footprints:
            # The only member that has to be materialised: rewriting the
            # model paths means parsing and re-emitting it. Footprints are
            # capped at 2 MiB by `storage.MAX_BYTES_BY_KIND`.
            data = _footprint_for_package(
                plan.identifier, _read_stored(plan.workspace_id, entry)
            )
            _add_member(archive, entry.member, data)
            install_size += len(data)

        for entry in plan.blobs:
            install_size += _copy_stored(archive, plan.workspace_id, entry)

    return install_size


# ---------------------------------------------------------------------
# Cache — content-addressed, one live package per workspace
#
# Each build leaves two files: `<fingerprint>.zip` and a `<fingerprint>.json`
# sidecar holding its digest and sizes. The sidecar is what makes a cache
# hit cheap: `repository.json` and `packages.json` need `download_sha256`,
# `download_size` and `install_size` and nothing else, so with it they are
# answered without opening the zip at all — and `package.zip` is streamed
# from disk rather than read into the worker.
# ---------------------------------------------------------------------


def _cache_dir(workspace_id: UUID) -> str:
    return os.path.join(settings().UPLOAD_DIR, "eda", str(workspace_id), "pcm")


def _cache_path(plan: Plan) -> str:
    return os.path.join(_cache_dir(plan.workspace_id), f"{plan.fingerprint}.zip")


def _sidecar_path(plan: Plan) -> str:
    return os.path.join(_cache_dir(plan.workspace_id), f"{plan.fingerprint}.json")


def _digest_file(path: str) -> tuple[str, int]:
    """`(sha256, size)` of a file, read a chunk at a time."""
    hasher = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while chunk := handle.read(_COPY_CHUNK_BYTES):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


def _install_size_of(path: str) -> int:
    """Uncompressed total, read from the zip's central directory only.

    Never inflates a member — the directory carries every `file_size` we
    need, so this stays O(member count) in memory whatever the archive
    weighs.
    """
    with zipfile.ZipFile(path) as archive:
        return sum(info.file_size for info in archive.infolist())


def _read_cache(plan: Plan) -> BuiltPackage | None:
    """The cached package, or None when there isn't a usable one.

    A missing sidecar is recoverable — the zip is the artifact, the
    sidecar only describes it — so the numbers are recomputed and written
    back rather than throwing the build away.

    A zip that will not open is NOT recoverable: it is a truncated or
    corrupt file (a killed process, a full disk, a half-finished copy),
    and the only right answer is to drop it and rebuild. Letting
    `BadZipFile` escape would turn a stale cache entry into a permanent
    500 for that workspace, since every later request would find the same
    bad file.
    """
    path = _cache_path(plan)
    if not os.path.exists(path):
        return None
    try:
        described = _read_sidecar(plan)
        if described is None:
            sha256, download_size = _digest_file(path)
            described = (sha256, download_size, _install_size_of(path))
            _write_sidecar(plan, described)
        sha256, download_size, install_size = described
        return BuiltPackage(
            plan=plan,
            sha256=sha256,
            download_size=download_size,
            install_size=install_size,
            path=path,
        )
    except (zipfile.BadZipFile, OSError, ValueError):
        _log.warning("pcm: discarding an unreadable cache entry for %s", plan.workspace_id)
        with contextlib.suppress(OSError):
            os.unlink(path)
        with contextlib.suppress(OSError):
            os.unlink(_sidecar_path(plan))
        return None


def _read_sidecar(plan: Plan) -> tuple[str, int, int] | None:
    """`(sha256, download_size, install_size)`, or None if unusable.

    Anything malformed reads as absent: the sidecar is a cache of a cache,
    and recomputing costs one pass over a file we already have.
    """
    try:
        with open(_sidecar_path(plan), "rb") as handle:
            described = json.loads(handle.read())
        sha256 = described["sha256"]
        download = int(described["download_size"])
        install = int(described["install_size"])
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if not isinstance(sha256, str) or len(sha256) != 64:
        return None
    if os.path.getsize(_cache_path(plan)) != download:
        # The zip changed without the sidecar following it.
        return None
    return sha256, download, install


def _write_sidecar(plan: Plan, described: tuple[str, int, int]) -> None:
    sha256, download_size, install_size = described
    payload = json_bytes(
        {
            "sha256": sha256,
            "download_size": download_size,
            "install_size": install_size,
        }
    )
    with contextlib.suppress(OSError):
        _atomic_write(_sidecar_path(plan), payload)


def _atomic_write(target: str, payload: bytes) -> None:
    """Write `payload` to `target` via a scratch file and a rename.

    Same idiom as `storage.store`: a per-writer scratch name plus an
    atomic replace, so a crashed write can never leave a truncated file
    under the canonical name.
    """
    target_dir = os.path.dirname(target)
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=target_dir, prefix=_SCRATCH_PREFIX, suffix=_SCRATCH_SUFFIX)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(tmp_path, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def _build_to_cache(plan: Plan) -> BuiltPackage | None:
    """Render straight onto disk. None when the cache dir is unusable.

    The zip is written into a scratch file and only then renamed into
    place, so a reader never sees a partial archive — and the worker never
    holds the finished package in memory at all, which is the whole point
    of building here rather than into a `BytesIO`.
    """
    target_dir = _cache_dir(plan.workspace_id)
    target = _cache_path(plan)
    try:
        os.makedirs(target_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=target_dir, prefix=_SCRATCH_PREFIX, suffix=_SCRATCH_SUFFIX
        )
    except OSError:
        _log.warning("pcm: cache directory unusable for %s", plan.workspace_id)
        return None

    try:
        with os.fdopen(fd, "wb") as handle:
            install_size = write_archive(plan, handle)
        sha256, download_size = _digest_file(tmp_path)
        os.replace(tmp_path, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise

    _write_sidecar(plan, (sha256, download_size, install_size))
    _prune(target_dir, keep=os.path.basename(target))
    return BuiltPackage(
        plan=plan,
        sha256=sha256,
        download_size=download_size,
        install_size=install_size,
        path=target,
    )


def _build_in_memory(plan: Plan) -> BuiltPackage:
    """Fallback for an unwritable cache directory — a read-only volume, a
    full disk. Correct, just not cheap; the package is held whole."""
    buffer = BytesIO()
    install_size = write_archive(plan, buffer)
    payload = buffer.getvalue()
    return BuiltPackage(
        plan=plan,
        sha256=hashlib.sha256(payload).hexdigest(),
        download_size=len(payload),
        install_size=install_size,
        data=payload,
    )


# How long a scratch file may sit before it is treated as debris. Long
# enough that a build in flight is never touched (they run in seconds,
# and this is an hour), short enough that a process killed mid-write
# doesn't leave a file behind forever.
_TMP_GRACE_SECONDS = 3600


def _prune(target_dir: str, *, keep: str) -> None:
    """Drop superseded packages and abandoned scratch files.

    Both matter because the fingerprint changes on every content change:
    without this a busy workspace leaves one stale zip per edit, and a
    build killed between `mkstemp` and `os.replace` leaves a scratch file
    that nothing will ever clean up. Scoped to this directory and to names
    this module writes — it never walks, and it never deletes the entry
    just written or its sidecar.

    Unlinking a file another request is streaming is safe on POSIX: the
    reader holds the descriptor. The narrow loser is a request that has
    resolved a path but not yet opened it, which fails and is retried.
    """
    keep_stem = keep.rsplit(".", 1)[0]
    cutoff = _now_seconds() - _TMP_GRACE_SECONDS
    with contextlib.suppress(OSError):
        for name in os.listdir(target_dir):
            path = os.path.join(target_dir, name)
            if name.endswith(".zip") or name.endswith(".json"):
                if name.rsplit(".", 1)[0] == keep_stem:
                    continue
                with contextlib.suppress(OSError):
                    os.unlink(path)
            elif name.startswith(_SCRATCH_PREFIX) and name.endswith(_SCRATCH_SUFFIX):
                with contextlib.suppress(OSError):
                    if os.stat(path).st_mtime < cutoff:
                        os.unlink(path)


def _now_seconds() -> float:
    """Wall clock, as a seam the tests can move."""
    return time.time()


def materialise(plan: Plan) -> BuiltPackage:
    """The package for `plan`, built only if it isn't already on disk.

    Touches the filesystem and nothing else, so the route can hand it to
    its threadpool while the request's database session stays put.

    Concurrent BUILDS are capped (`_BUILD_SLOTS`). A build is the one
    expensive thing here — it deflates every file the workspace owns, and
    `MAX_CONTENT_BYTES` allows 200 MiB of them — while the threadpool is
    40 slots wide and the archive route allows 30 requests a minute per
    address. Without the cap a burst of cache misses could run dozens of
    builds at once. Cache HITS are not capped: they neither build nor
    buffer, they stream a file off disk.
    """
    cached = _read_cache(plan)
    if cached is not None:
        return cached

    if not _BUILD_SLOTS.acquire(timeout=_BUILD_WAIT_SECONDS):
        _log.warning("pcm: build queue full, refusing a build for %s", plan.workspace_id)
        _unavailable("the package builder is busy — try again shortly")
    try:
        # Re-check under the semaphore: while this request waited, the
        # build it was queued behind may have been for the same content.
        cached = _read_cache(plan)
        if cached is not None:
            return cached
        built = _build_to_cache(plan)
        return built if built is not None else _build_in_memory(plan)
    finally:
        _BUILD_SLOTS.release()


def build_package(db: Session, *, ws) -> BuiltPackage:
    """`plan_package` + `materialise` in one call, for synchronous callers.

    The routes split the two so only the filesystem half runs off-thread;
    everything else wants the whole thing.
    """
    return materialise(plan_package(db, ws=ws))


# ---------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------


def json_bytes(payload: Any) -> bytes:
    """Serialise a document to the exact bytes that will be served.

    `repository.json` publishes the SHA-256 of `packages.json`, so the
    hashed bytes and the served bytes have to be the same ones — hence a
    single deterministic serialiser rather than letting `JSONResponse`
    re-encode.
    """
    return json.dumps(
        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def _maintainer() -> dict[str, Any]:
    """The schema's Contact shape: both keys are required, and `contact`
    may be empty — there is no address to publish for a self-hosted
    repository that only its own workspace can read."""
    return {"name": "stockManager", "contact": {}}


def _package_name(plan: Plan) -> str:
    return f"{plan.workspace_name} (stockManager)"[:_MAX_NAME]


def metadata_document(plan: Plan) -> dict[str, Any]:
    """`metadata.json`, as it appears INSIDE the zip.

    Identical to the `packages.json` entry minus the `download_*` and
    `install_size` fields, which the PCM docs are explicit must appear
    only in the repository's copy — they describe the archive and cannot
    be inside it.
    """
    return {
        "name": _package_name(plan),
        "description": (
            f"KiCad symbols, footprints and 3D models hosted by the "
            f"{plan.workspace_name} stockManager workspace."
        )[:_MAX_DESCRIPTION],
        "description_full": _description_full(plan)[:_MAX_DESCRIPTION_FULL],
        "identifier": plan.identifier,
        "type": PACKAGE_TYPE,
        "author": _maintainer(),
        "license": PACKAGE_LICENSE,
        "resources": {},
        "versions": [
            {
                "version": plan.version,
                "status": "stable",
                "kicad_version": KICAD_VERSION,
            }
        ],
    }


def _description_full(plan: Plan) -> str:
    """The long description the PCM shows on the package page.

    Worth spending words on: it is the only place a user reads the two
    things they need to know after installing — what the libraries are
    called, and that SPICE needs one path variable set by hand.
    """
    return (
        f"Generated from the {plan.workspace_name} workspace in stockManager. "
        "Each part category becomes one symbol library and one footprint "
        f"library, registered by the Plugin and Content Manager as "
        f"{kicad_refs.PCM_NICKNAME_PREFIX}{kicad_refs.LIBRARY_PREFIX}<category>; "
        f"entries with no category land in "
        f"{kicad_refs.library_nickname(None)}. 3D models resolve on their own. "
        "To use the SPICE models, add a path variable named "
        f"{kicad_refs.SPICE_PATH_VAR.strip('${}')} pointing at "
        f"{kicad_refs.pcm_spice_dir(plan.identifier)} in "
        "Preferences > Configure Paths. Reinstall or update the package to "
        "pick up library changes made in stockManager."
    )


def packages_document(built: BuiltPackage, *, download_url: str) -> dict[str, Any]:
    """`packages.json` — the repository's package list.

    A workspace with no library content publishes an empty list rather
    than an empty package: the PCM would happily install the latter, and
    a user would end up with registered libraries containing nothing.
    """
    if built.plan.is_empty:
        return {"packages": []}

    package = metadata_document(built.plan)
    package["versions"] = [
        {
            **package["versions"][0],
            "download_url": download_url,
            "download_sha256": built.sha256,
            "download_size": built.download_size,
            "install_size": built.install_size,
        }
    ]
    return {"packages": [package]}


def repository_document(
    plan: Plan,
    *,
    packages_url: str,
    packages_sha256: str,
) -> dict[str, Any]:
    """`repository.json` — the document a user pastes the URL of.

    `update_timestamp` is what the PCM compares to decide whether to
    re-fetch `packages.json`, so it tracks the same newest-change moment
    the version does rather than the wall clock: an unchanged workspace
    answers identically every time, and a changed one always differs.
    """
    moment = datetime.fromtimestamp(plan.update_timestamp, tz=timezone.utc)
    return {
        "name": _package_name(plan),
        "maintainer": _maintainer(),
        "packages": {
            "url": packages_url,
            "sha256": packages_sha256,
            "update_timestamp": plan.update_timestamp,
            "update_time_utc": moment.strftime("%Y-%m-%d %H:%M:%S"),
        },
    }
