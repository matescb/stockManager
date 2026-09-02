"""EDA libraries — `/api/eda` plus the per-part config under `/api/parts`.

Two routers in one module, mounted separately in `main.py` (the shape
`sourcing.py` uses): `router` owns the workspace-wide library at
`/api/eda`, `parts_router` owns `/api/parts/{part_id}/eda`. They live
together because the part config is meaningless without the library it
points into, and splitting them would put one small service behind two
route modules.

Thin routes: every query, conflict check and file validation lives in
`app/domain/eda/{service,storage}.py`. Writes are member-gated by
`_member_gate` in `main.py` and rate-limited per workspace.

Uploads take the text-CAD lane in `domain/eda/storage.py`, NOT the
magic-byte validators in `attachments.py` / `parts/services/assets.py`.
KiCad libraries are text and have no magic number; the binary
allow-lists those two enforce are load-bearing for formats a browser
will render, and must not be loosened to let these through.
"""
from __future__ import annotations

import os
from uuid import UUID

from fastapi import (
    APIRouter,
    File,
    Form,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from app.api.routes._eda_shared import audit as _audit
from app.api.routes._eda_shared import patch_comment as _patch_comment
from app.api.routes._eda_shared import read_upload as _read_upload
from app.api.routes._parts_shared import get_part as _get_part
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.errors import ErrorCodes, raise_http
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import Envelope, ok
from app.domain.eda import service as eda_service
from app.domain.eda import storage
from app.domain.eda.models import EdaDatafile, EdaFootprint, EdaSymbol
from app.domain.eda.schemas import (
    EdaDatafileOut,
    EdaDatafilePatch,
    EdaFootprintModelIn,
    EdaFootprintModelOut,
    EdaFootprintOut,
    EdaFootprintPatch,
    EdaSymbolOut,
    EdaSymbolPatch,
    PartEdaIn,
    PartEdaOut,
)

router = APIRouter()
parts_router = APIRouter()

# Uploads are heavier than a JSON write (a megabyte of parsing), so they
# get their own tighter bucket.
_UPLOAD_RATE = "20/minute"
_CONFIG_RATE = "60/minute"


# ---------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------


@router.get("/symbols")
def list_symbols(
    db: DbSession,
    ws: CurrentWorkspace,
    include_archived: bool = Query(default=False),
    limit: int = Query(default=200, le=1000),
) -> Envelope[list[EdaSymbolOut]]:
    rows = eda_service.list_entries(
        db, ws=ws, Model=EdaSymbol, include_archived=include_archived, limit=limit
    )
    return ok([EdaSymbolOut.model_validate(row) for row in rows])


@router.post("/symbols", status_code=status.HTTP_201_CREATED)
@limiter.limit(_UPLOAD_RATE, key_func=workspace_key)
async def upload_symbol(
    request: Request,
    response: Response,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
    file: UploadFile = File(...),
    name: str | None = Form(default=None, max_length=200),
    category_id: UUID | None = Form(default=None),
) -> Envelope[EdaSymbolOut]:
    """Upload one schematic symbol.

    Accepts a `.kicad_sym` carrying a single symbol, or a bare
    `(symbol …)` node. The stored file is always the bare entry
    re-emitted, so the library has one shape however it was fed. A
    multi-symbol library is refused with `eda.multiple_symbols` — that
    needs the zip importer, not this endpoint.

    Re-uploading identical bytes under the same name answers 200 with
    the existing row instead of 201.
    """
    contents = await _read_upload(file, kind=storage.SYMBOL_KIND)
    # Parsing + re-emitting attacker-supplied text is CPU-bound; keep it
    # off the event loop — prod runs a single uvicorn worker, so a slow
    # validation inline would stall the whole API (P2 security review).
    parsed_name, data = await run_in_threadpool(storage.canonical_symbol, contents)
    # Digest now, write the blob only after the row insert succeeds —
    # writing first left an orphan file on every 409/404 rejection
    # (P2 security review MEDIUM-2).
    sha, size = storage.digest(data)

    row, created = eda_service.upload_entry(
        db,
        ws=ws,
        Model=EdaSymbol,
        user_id=user.id,
        name=name or parsed_name,
        sha256=sha,
        size_bytes=size,
        category_id=category_id,
    )
    storage.store(ws.id, data, kind=storage.SYMBOL_KIND)
    if created:
        _audit(
            request,
            db,
            ws,
            user,
            action="eda_symbol.uploaded",
            target_type="eda_symbol",
            target_id=row.id,
            comment=f"sha256={sha}",
        )
    else:
        response.status_code = status.HTTP_200_OK
    return ok(EdaSymbolOut.model_validate(row))


@router.patch("/symbols/{symbol_id}")
@limiter.limit(_CONFIG_RATE, key_func=workspace_key)
def patch_symbol(
    request: Request,
    symbol_id: UUID,
    payload: EdaSymbolPatch,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[EdaSymbolOut]:
    row = eda_service.update_entry(
        db, ws=ws, Model=EdaSymbol, entry_id=symbol_id, user_id=user.id, payload=payload
    )
    _audit(
        request,
        db,
        ws,
        user,
        action="eda_symbol.updated",
        target_type="eda_symbol",
        target_id=row.id,
        comment=_patch_comment(payload),
    )
    return ok(EdaSymbolOut.model_validate(row))


@router.post("/symbols/{symbol_id}/archive")
@limiter.limit(_CONFIG_RATE, key_func=workspace_key)
def archive_symbol(
    request: Request,
    symbol_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[None]:
    row = eda_service.archive_entry(
        db, ws=ws, Model=EdaSymbol, entry_id=symbol_id, user_id=user.id
    )
    _audit(
        request,
        db,
        ws,
        user,
        action="eda_symbol.archived",
        target_type="eda_symbol",
        target_id=row.id,
    )
    return ok(None, "archived")


@router.post("/symbols/{symbol_id}/restore")
@limiter.limit(_CONFIG_RATE, key_func=workspace_key)
def restore_symbol(
    request: Request,
    symbol_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[None]:
    """Un-archive. 409 when the freed name has since been claimed by
    another active symbol — see `service.restore_entry`."""
    row = eda_service.restore_entry(
        db, ws=ws, Model=EdaSymbol, entry_id=symbol_id, user_id=user.id
    )
    _audit(
        request,
        db,
        ws,
        user,
        action="eda_symbol.restored",
        target_type="eda_symbol",
        target_id=row.id,
    )
    return ok(None, "restored")


# ---------------------------------------------------------------------
# Footprints
# ---------------------------------------------------------------------


@router.get("/footprints")
def list_footprints(
    db: DbSession,
    ws: CurrentWorkspace,
    include_archived: bool = Query(default=False),
    limit: int = Query(default=200, le=1000),
) -> Envelope[list[EdaFootprintOut]]:
    rows = eda_service.list_entries(
        db, ws=ws, Model=EdaFootprint, include_archived=include_archived, limit=limit
    )
    return ok([EdaFootprintOut.model_validate(row) for row in rows])


@router.post("/footprints", status_code=status.HTTP_201_CREATED)
@limiter.limit(_UPLOAD_RATE, key_func=workspace_key)
async def upload_footprint(
    request: Request,
    response: Response,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
    file: UploadFile = File(...),
    name: str | None = Form(default=None, max_length=200),
    category_id: UUID | None = Form(default=None),
) -> Envelope[EdaFootprintOut]:
    """Upload one `.kicad_mod` footprint. `(module …)` — the pre-6.0
    spelling — is accepted too."""
    contents = await _read_upload(file, kind=storage.FOOTPRINT_KIND)
    # Off the event loop for the same reason as the symbol handler.
    parsed_name, data = await run_in_threadpool(storage.canonical_footprint, contents)
    # Digest first, blob written after the row insert — see the symbol
    # handler.
    sha, size = storage.digest(data)

    row, created = eda_service.upload_entry(
        db,
        ws=ws,
        Model=EdaFootprint,
        user_id=user.id,
        name=name or parsed_name,
        sha256=sha,
        size_bytes=size,
        category_id=category_id,
    )
    storage.store(ws.id, data, kind=storage.FOOTPRINT_KIND)
    if created:
        _audit(
            request,
            db,
            ws,
            user,
            action="eda_footprint.uploaded",
            target_type="eda_footprint",
            target_id=row.id,
            comment=f"sha256={sha}",
        )
    else:
        response.status_code = status.HTTP_200_OK
    return ok(EdaFootprintOut.model_validate(row))


@router.patch("/footprints/{footprint_id}")
@limiter.limit(_CONFIG_RATE, key_func=workspace_key)
def patch_footprint(
    request: Request,
    footprint_id: UUID,
    payload: EdaFootprintPatch,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[EdaFootprintOut]:
    row = eda_service.update_entry(
        db,
        ws=ws,
        Model=EdaFootprint,
        entry_id=footprint_id,
        user_id=user.id,
        payload=payload,
    )
    _audit(
        request,
        db,
        ws,
        user,
        action="eda_footprint.updated",
        target_type="eda_footprint",
        target_id=row.id,
        comment=_patch_comment(payload),
    )
    return ok(EdaFootprintOut.model_validate(row))


@router.post("/footprints/{footprint_id}/archive")
@limiter.limit(_CONFIG_RATE, key_func=workspace_key)
def archive_footprint(
    request: Request,
    footprint_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[None]:
    row = eda_service.archive_entry(
        db, ws=ws, Model=EdaFootprint, entry_id=footprint_id, user_id=user.id
    )
    _audit(
        request,
        db,
        ws,
        user,
        action="eda_footprint.archived",
        target_type="eda_footprint",
        target_id=row.id,
    )
    return ok(None, "archived")


@router.post("/footprints/{footprint_id}/restore")
@limiter.limit(_CONFIG_RATE, key_func=workspace_key)
def restore_footprint(
    request: Request,
    footprint_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[None]:
    row = eda_service.restore_entry(
        db, ws=ws, Model=EdaFootprint, entry_id=footprint_id, user_id=user.id
    )
    _audit(
        request,
        db,
        ws,
        user,
        action="eda_footprint.restored",
        target_type="eda_footprint",
        target_id=row.id,
    )
    return ok(None, "restored")


# ---------------------------------------------------------------------
# Footprint ↔ 3D model links
# ---------------------------------------------------------------------


@router.get("/footprints/{footprint_id}/models")
def list_footprint_models(
    footprint_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
) -> Envelope[list[EdaFootprintModelOut]]:
    footprint = eda_service.get_entry(
        db, ws=ws, Model=EdaFootprint, entry_id=footprint_id
    )
    rows = eda_service.list_footprint_models(db, ws=ws, footprint=footprint)
    return ok([EdaFootprintModelOut.model_validate(row) for row in rows])


@router.post("/footprints/{footprint_id}/models")
@limiter.limit(_CONFIG_RATE, key_func=workspace_key)
def link_footprint_model(
    request: Request,
    footprint_id: UUID,
    payload: EdaFootprintModelIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[list[EdaFootprintModelOut]]:
    """Attach a STEP or WRL model to a footprint. Idempotent — re-linking
    a pair moves it to the given position."""
    footprint = eda_service.link_footprint_model(
        db,
        ws=ws,
        footprint_id=footprint_id,
        datafile_id=payload.datafile_id,
        position=payload.position,
        user_id=user.id,
    )
    _audit(
        request,
        db,
        ws,
        user,
        action="eda_footprint.updated",
        target_type="eda_footprint",
        target_id=footprint.id,
        comment=f"model_linked={payload.datafile_id}",
    )
    rows = eda_service.list_footprint_models(db, ws=ws, footprint=footprint)
    return ok([EdaFootprintModelOut.model_validate(row) for row in rows])


@router.delete("/footprints/{footprint_id}/models/{datafile_id}")
@limiter.limit(_CONFIG_RATE, key_func=workspace_key)
def unlink_footprint_model(
    request: Request,
    footprint_id: UUID,
    datafile_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[None]:
    footprint = eda_service.unlink_footprint_model(
        db,
        ws=ws,
        footprint_id=footprint_id,
        datafile_id=datafile_id,
        user_id=user.id,
    )
    _audit(
        request,
        db,
        ws,
        user,
        action="eda_footprint.updated",
        target_type="eda_footprint",
        target_id=footprint.id,
        comment=f"model_unlinked={datafile_id}",
    )
    return ok(None, "deleted")


# ---------------------------------------------------------------------
# Data files (3D models + SPICE)
# ---------------------------------------------------------------------


@router.get("/datafiles")
def list_datafiles(
    db: DbSession,
    ws: CurrentWorkspace,
    include_archived: bool = Query(default=False),
    limit: int = Query(default=200, le=1000),
) -> Envelope[list[EdaDatafileOut]]:
    rows = eda_service.list_entries(
        db, ws=ws, Model=EdaDatafile, include_archived=include_archived, limit=limit
    )
    return ok([EdaDatafileOut.model_validate(row) for row in rows])


@router.post("/datafiles", status_code=status.HTTP_201_CREATED)
@limiter.limit(_UPLOAD_RATE, key_func=workspace_key)
async def upload_datafile(
    request: Request,
    response: Response,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
    file: UploadFile = File(...),
    name: str | None = Form(default=None, max_length=200),
) -> Envelope[EdaDatafileOut]:
    """Upload a 3D model (STEP / WRL) or a SPICE model.

    The kind comes from the filename extension — the three formats share
    no reliable byte-level discriminator, so guessing from content would
    be worse than asking. `name` defaults to the uploaded filename.
    """
    kind = storage.datafile_kind_from_filename(file.filename)
    contents = await _read_upload(file, kind=kind)
    data = storage.validated_datafile(kind, contents)
    # Digest first, blob written after the row insert — see the symbol
    # handler.
    sha, size = storage.digest(data)

    row, created = eda_service.upload_entry(
        db,
        ws=ws,
        Model=EdaDatafile,
        user_id=user.id,
        name=name or _basename(file.filename) or f"{kind}-{sha[:8]}",
        sha256=sha,
        size_bytes=size,
        kind=kind,
    )
    storage.store(ws.id, data, kind=kind)
    if created:
        _audit(
            request,
            db,
            ws,
            user,
            action="eda_datafile.uploaded",
            target_type="eda_datafile",
            target_id=row.id,
            comment=f"kind={kind},sha256={sha}",
        )
    else:
        response.status_code = status.HTTP_200_OK
    return ok(EdaDatafileOut.model_validate(row))


def _basename(filename: str | None) -> str:
    """The client-supplied filename, stripped of any path and capped to
    the column width. Never used to build a path — files are stored
    under their content hash — only as the row's display name."""
    if not filename:
        return ""
    return os.path.basename(filename.replace("\\", "/")).strip()[:200]


@router.patch("/datafiles/{datafile_id}")
@limiter.limit(_CONFIG_RATE, key_func=workspace_key)
def patch_datafile(
    request: Request,
    datafile_id: UUID,
    payload: EdaDatafilePatch,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[EdaDatafileOut]:
    row = eda_service.update_entry(
        db,
        ws=ws,
        Model=EdaDatafile,
        entry_id=datafile_id,
        user_id=user.id,
        payload=payload,
    )
    _audit(
        request,
        db,
        ws,
        user,
        action="eda_datafile.updated",
        target_type="eda_datafile",
        target_id=row.id,
        comment=_patch_comment(payload),
    )
    return ok(EdaDatafileOut.model_validate(row))


@router.post("/datafiles/{datafile_id}/archive")
@limiter.limit(_CONFIG_RATE, key_func=workspace_key)
def archive_datafile(
    request: Request,
    datafile_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[None]:
    row = eda_service.archive_entry(
        db, ws=ws, Model=EdaDatafile, entry_id=datafile_id, user_id=user.id
    )
    _audit(
        request,
        db,
        ws,
        user,
        action="eda_datafile.archived",
        target_type="eda_datafile",
        target_id=row.id,
    )
    return ok(None, "archived")


@router.post("/datafiles/{datafile_id}/restore")
@limiter.limit(_CONFIG_RATE, key_func=workspace_key)
def restore_datafile(
    request: Request,
    datafile_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[None]:
    row = eda_service.restore_entry(
        db, ws=ws, Model=EdaDatafile, entry_id=datafile_id, user_id=user.id
    )
    _audit(
        request,
        db,
        ws,
        user,
        action="eda_datafile.restored",
        target_type="eda_datafile",
        target_id=row.id,
    )
    return ok(None, "restored")


# ---------------------------------------------------------------------
# Serving stored files
#
# Content-addressed under {UPLOAD_DIR}/eda/{ws_id}/{sha}.{ext}, so the
# immutable cache header is safe and an overwrite can't break an
# in-flight request. Mechanics copied from
# `parts_assets.py::get_provider_asset`, with one deliberate difference:
# nothing here is ever served inline. A `.kicad_sym` is attacker-supplied
# text on our own origin — rendering it in a tab would be a same-origin
# XSS, and no viewer wants it inline anyway.
# ---------------------------------------------------------------------


@router.get("/files/{ws_id}/{filename}")
def get_eda_file(
    ws_id: UUID,
    filename: str,
    ws: CurrentWorkspace,
    name: str | None = Query(default=None, max_length=120),
):
    # Workspace-scoped: an operator can only fetch files under their own
    # workspace's folder. The `ws` dep already proves membership in the
    # request's current workspace; this matches them.
    if ws_id != ws.id:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCodes.EDA_FILE_NOT_FOUND,
            message="file not found",
        )
    # Disallow path traversal — filename must be a flat content-addressed name.
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            code=ErrorCodes.EDA_INVALID_FILENAME,
            message="invalid filename",
        )

    abs_path = storage.path_for(ws_id, filename)
    if not os.path.isfile(abs_path):
        raise_http(
            status.HTTP_404_NOT_FOUND,
            code=ErrorCodes.EDA_FILE_NOT_FOUND,
            message="file not found",
        )

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    headers = {
        # Content-addressed → safe to cache for a year, never re-revalidate.
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
    }
    if name:
        # Restrict to a safe subset and append the stored extension so the
        # saved file opens in KiCad rather than a text editor.
        # ASCII-only: str.isalnum() alone admits Unicode letters, which
        # blow up Starlette's latin-1 header encoding with a 500.
        safe = "".join(
            c for c in name if c.isascii() and (c.isalnum() or c in "._-")
        )[:80] or "library"
        suffix = f".{ext}" if ext and not safe.lower().endswith(f".{ext}") else ""
        headers["Content-Disposition"] = f'attachment; filename="{safe}{suffix}"'
    else:
        # Set the header explicitly rather than relying on Starlette's
        # `content_disposition_type` kwarg — that param was added in
        # 0.36+ and silently no-ops on older versions, leaving the
        # response without a Content-Disposition at all.
        headers["Content-Disposition"] = "attachment"
    return FileResponse(abs_path, media_type="application/octet-stream", headers=headers)


# ---------------------------------------------------------------------
# Per-part EDA configuration — mounted under /api/parts
# ---------------------------------------------------------------------


@parts_router.get("/{part_id}/eda")
def get_part_eda(
    part_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
) -> Envelope[PartEdaOut | None]:
    """The part's EDA configuration, or `data: null` when it has none.

    Archived parts included — this is a read-only surface and the detail
    page still loads for them (BE2-016).
    """
    part = _get_part(db, ws.id, part_id, include_archived=True)
    config = eda_service.get_part_eda(db, ws=ws, part=part)
    return ok(PartEdaOut.model_validate(config) if config is not None else None)


@parts_router.put("/{part_id}/eda")
@limiter.limit(_CONFIG_RATE, key_func=workspace_key)
def put_part_eda(
    request: Request,
    part_id: UUID,
    payload: PartEdaIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[PartEdaOut]:
    """Replace the part's EDA configuration, creating it if absent.

    A full replacement, not a merge — an omitted field is written as its
    default. See `PartEdaIn`.
    """
    part = _get_part(db, ws.id, part_id)
    config = eda_service.upsert_part_eda(
        db, ws=ws, part=part, user_id=user.id, payload=payload
    )
    # Targets the PART, not the part_eda row: the config is deleted and
    # recreated freely, so its own id is ephemeral and a trail keyed on it
    # would fragment. The part id is what an auditor searches for.
    _audit(
        request,
        db,
        ws,
        user,
        action="part_eda.updated",
        target_type="part_eda",
        target_id=part.id,
        comment=_patch_comment(payload),
    )
    return ok(PartEdaOut.model_validate(config))


@parts_router.delete("/{part_id}/eda")
@limiter.limit(_CONFIG_RATE, key_func=workspace_key)
def delete_part_eda(
    request: Request,
    part_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[None]:
    part = _get_part(db, ws.id, part_id)
    eda_service.delete_part_eda(db, ws=ws, part=part)
    _audit(
        request,
        db,
        ws,
        user,
        action="part_eda.deleted",
        target_type="part_eda",
        target_id=part.id,
    )
    return ok(None, "deleted")
