"""Vendor imports — `POST /api/eda/import` and the two part-bound ones.

Three ways to fill a part's CAD slots without uploading four files by
hand:

* `POST /api/parts/{part_id}/eda/import` — a SnapEDA / SamacSys /
  UltraLibrarian zip, narrowed to the one symbol and one footprint the
  part needs, then wired into `part_eda`.
* `POST /api/eda/import` — the same zip (or a bare multi-symbol
  `.kicad_sym`) imported as library entries, wiring nothing.
* `POST /api/parts/{part_id}/eda/fetch-lcsc` — the same result fetched
  from EasyEDA by LCSC part number.

Mounted alongside `eda.py` under the same two prefixes; it is a separate
module because the import pipeline is a different shape from the CRUD
and neither file wants to be 1,000 lines.

Everything CPU-bound — zip inflation, s-expression parsing, re-emission
— and everything network-bound runs through `run_in_threadpool`. Prod is
a single uvicorn worker, so a 50 MiB archive parsed on the event loop
would stall every other request (the reason the P2 upload routes do the
same).

Audit: an import can touch many rows at once, so per-entry
`eda_*.uploaded` rows are written up to `_AUDIT_ROW_LIMIT` created rows
and collapse into a single `eda_library.imported` row past it — an
audit trail nobody can read is worse than a summary. The part-bound
endpoints always add their own `part_eda.imported` row on top.
"""
from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, File, Form, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool

from app.api.routes._eda_shared import audit as _audit
from app.api.routes._eda_shared import read_upload as _read_upload
from app.api.routes._parts_shared import get_part as _get_part
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.errors import ErrorCodes, raise_http
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import Envelope, ok
from app.domain.eda import importer, lcsc, vendor_zip
from app.domain.eda.importer import CreatedRow, ImportResult
from app.domain.eda.schemas import (
    EdaImportRowOut,
    EdaImportSkipOut,
    EdaLibraryImportOut,
    LcscFetchIn,
    PartEdaImportOut,
)

router = APIRouter()
parts_router = APIRouter()

# An archive costs far more than a single upload — inflate, parse every
# entry, re-emit, hash, write — so it gets a tighter bucket than the
# `20/minute` the single-file routes use.
_IMPORT_RATE = "10/minute"
# Tighter still: this one leaves the building.
_LCSC_RATE = "5/minute"

# Past this many created rows, per-entry audit rows stop being a trail
# and start being noise; one summary row replaces them.
_AUDIT_ROW_LIMIT = 20

# `max_bytes_for` has no archive entry, so this resolves to the global
# MAX_UPLOAD_BYTES — the cap an operator already tunes for uploads.
_ARCHIVE_KIND = "archive"

_AUDIT_ACTIONS = {
    "symbols": ("eda_symbol.uploaded", "eda_symbol"),
    "footprints": ("eda_footprint.uploaded", "eda_footprint"),
    "datafiles": ("eda_datafile.uploaded", "eda_datafile"),
}


# ---------------------------------------------------------------------
# Shared plumbing
# ---------------------------------------------------------------------


def _row_out(row: CreatedRow) -> EdaImportRowOut:
    return EdaImportRowOut(id=row.id, name=row.name, created=row.created, kind=row.kind)


def _skips_out(result: ImportResult) -> list[EdaImportSkipOut]:
    return [
        EdaImportSkipOut(filename=item.filename, reason=item.reason)
        for item in result.skipped
    ]


def _audit_entries(request, db, ws, user, result: ImportResult, *, vendor: str) -> None:
    """Record what the import created — per entry, or one summary row."""
    created = [
        (bucket, row)
        for bucket in _AUDIT_ACTIONS
        for row in getattr(result, bucket)
        if row.created
    ]
    if not created:
        return
    if len(created) > _AUDIT_ROW_LIMIT:
        _audit(
            request,
            db,
            ws,
            user,
            action="eda_library.imported",
            target_type="eda_library",
            target_id=ws.id,
            comment=(
                f"vendor={vendor},symbols={len(result.symbols)},"
                f"footprints={len(result.footprints)},datafiles={len(result.datafiles)}"
            ),
        )
        return
    for bucket, row in created:
        action, target_type = _AUDIT_ACTIONS[bucket]
        _audit(
            request,
            db,
            ws,
            user,
            action=action,
            target_type=target_type,
            target_id=row.id,
            comment=f"sha256={row.sha256}",
        )


def _part_import_out(result: ImportResult) -> PartEdaImportOut:
    return PartEdaImportOut(
        vendor=result.vendor,
        symbol=_row_out(result.symbols[0]) if result.symbols else None,
        footprint=_row_out(result.footprints[0]) if result.footprints else None,
        datafiles=[_row_out(row) for row in result.datafiles],
        part_eda_updated=result.part_eda_updated,
        skipped=_skips_out(result),
    )


def _finish_part_import(
    request, db, ws, user, part, *, result: ImportResult, overwrite: bool
) -> PartEdaImportOut:
    """Wire the part, write the audit trail, shape the response."""
    result.part_eda_updated = importer.wire_part(
        db, ws=ws, part=part, user_id=user.id, result=result, overwrite=overwrite
    )
    _audit_entries(request, db, ws, user, result, vendor=result.vendor)
    _audit(
        request,
        db,
        ws,
        user,
        action="part_eda.imported",
        target_type="part_eda",
        target_id=part.id,
        # Targets the PART for the same reason `part_eda.updated` does —
        # the config row's own id is ephemeral.
        comment=f"vendor={result.vendor},files={len(result.rows)}",
    )
    return _part_import_out(result)


# ---------------------------------------------------------------------
# Part-bound zip import
# ---------------------------------------------------------------------


@parts_router.post("/{part_id}/eda/import")
@limiter.limit(_IMPORT_RATE, key_func=workspace_key)
async def import_part_archive(
    request: Request,
    part_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
    file: UploadFile = File(...),
    overwrite: bool = Form(default=False),
    category_id: UUID | None = Form(default=None),
) -> Envelope[PartEdaImportOut]:
    """Import a vendor CAD zip and wire it into this part.

    Takes one symbol, one footprint, whatever 3D and SPICE models the
    archive carries, and points the part at them. Occupied slots are
    left alone unless `overwrite` — an import is additive by default,
    because the usual case is adding a 3D model to a part someone
    already configured. `value`, `keywords` and the exclusion flags are
    never touched: no vendor archive knows better than the user.

    A member we can't place is reported in `skipped` rather than failing
    the archive. An archive holding several symbols with no way to tell
    which one belongs to this part is a 422 — see
    `vendor_zip.narrow_to_part`.
    """
    part = _get_part(db, ws.id, part_id)
    raw = await _read_upload(file, kind=_ARCHIVE_KIND)
    hints = [_stem(file.filename), part.mpn or "", part.internal_part_number or ""]
    plan = await run_in_threadpool(_plan_for_part, raw, file.filename, hints)

    result = importer.import_plan(
        db,
        ws=ws,
        user_id=user.id,
        plan=plan,
        source=plan.vendor,
        category_id=category_id,
    )
    return ok(
        _finish_part_import(
            request, db, ws, user, part, result=result, overwrite=overwrite
        )
    )


def _plan_for_part(raw: bytes, filename: str | None, hints: list[str]):
    plan = vendor_zip.read_archive(raw, filename=filename)
    return vendor_zip.narrow_to_part(plan, hints=hints)


def _stem(filename: str | None) -> str:
    """The archive's own name, minus path, extension and the `LIB_`
    prefix SamacSys puts on every download — what's left is the MPN,
    which is the best hint we have for picking a symbol."""
    if not filename:
        return ""
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return stem[4:] if stem.upper().startswith("LIB_") else stem


# ---------------------------------------------------------------------
# Library-level import
# ---------------------------------------------------------------------


@router.post("/import")
@limiter.limit(_IMPORT_RATE, key_func=workspace_key)
async def import_library(
    request: Request,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
    file: UploadFile = File(...),
    category_id: UUID | None = Form(default=None),
) -> Envelope[EdaLibraryImportOut]:
    """Import a vendor zip, or a multi-symbol `.kicad_sym`, as a library.

    Everything the file offers becomes a library entry; nothing is wired
    to a part. This is where the single-symbol upload route points when
    it refuses a multi-symbol `.kicad_sym`.

    Audit: one row per created entry, up to 20 of them; past that a
    single `eda_library.imported` row carrying the counts, because 200
    near-identical rows are not a trail anyone reads.
    """
    raw = await _read_upload(file, kind=_ARCHIVE_KIND)
    plan = await run_in_threadpool(_plan_for_library, raw, file.filename)

    result = importer.import_plan(
        db, ws=ws, user_id=user.id, plan=plan, source=plan.vendor, category_id=category_id
    )
    _audit_entries(request, db, ws, user, result, vendor=plan.vendor)
    return ok(
        EdaLibraryImportOut(
            vendor=plan.vendor,
            created=result.created_count,
            reused=result.reused_count,
            symbols=[_row_out(row) for row in result.symbols],
            footprints=[_row_out(row) for row in result.footprints],
            datafiles=[_row_out(row) for row in result.datafiles],
            skipped=_skips_out(result),
        )
    )


def _plan_for_library(raw: bytes, filename: str | None):
    """A zip, or the bare `.kicad_sym` the single-upload route rejects.

    Sniffing the local-file-header magic rather than trusting the
    extension: the file arrives from a browser file picker, and the two
    formats can't be confused once you've looked at four bytes.
    """
    if raw[:2] == b"PK":
        return vendor_zip.read_archive(raw, filename=filename)
    return vendor_zip.read_symbol_library(raw, filename=filename)


# ---------------------------------------------------------------------
# LCSC / EasyEDA
# ---------------------------------------------------------------------


@parts_router.post("/{part_id}/eda/fetch-lcsc")
@limiter.limit(_LCSC_RATE, key_func=workspace_key)
async def fetch_lcsc(
    request: Request,
    part_id: UUID,
    payload: LcscFetchIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[PartEdaImportOut]:
    """Fetch an LCSC part from EasyEDA and wire it into this part.

    Same wiring rules and same response shape as the zip importer, with
    `vendor: "easyeda"`. The fetch is bounded twice — a deadline inside
    the worker and a hard wait here — because `easyeda2kicad` uses
    `urllib` timeouts we can't reach into.
    """
    part = _get_part(db, ws.id, part_id)
    plan = await _fetch_lcsc_plan(payload.lcsc_id)

    result = importer.import_plan(
        db, ws=ws, user_id=user.id, plan=plan, source=lcsc.SOURCE
    )
    return ok(
        _finish_part_import(
            request, db, ws, user, part, result=result, overwrite=payload.overwrite
        )
    )


async def _fetch_lcsc_plan(lcsc_id: str):
    try:
        return await asyncio.wait_for(
            run_in_threadpool(lcsc.fetch_plan, lcsc_id),
            # Headroom over the worker's own budget, so the per-stage
            # deadline checks are what actually govern.
            timeout=lcsc.HARD_TIMEOUT_SECONDS,
        )
    except lcsc.LcscNotFound:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCodes.EDA_LCSC_NOT_FOUND,
            message=(
                f"EasyEDA has no CAD data for {lcsc_id} "
                "(or the service is currently unreachable)"
            ),
        )
    except (lcsc.LcscUnavailable, TimeoutError):
        # TimeoutError covers asyncio.wait_for firing: the worker thread
        # runs on to its own urllib timeout, but the client isn't kept
        # waiting for it.
        raise_http(
            status.HTTP_502_BAD_GATEWAY,
            code=ErrorCodes.EDA_LCSC_UNAVAILABLE,
            message="EasyEDA could not be reached — try again in a moment",
        )
