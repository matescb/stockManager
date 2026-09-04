"""EDA 3D preview — `GET /api/eda/datafiles/{id}/preview.glb`.

A third router on the `/api/eda` surface (alongside `eda.py` and
`eda_import.py`), split out so the binary STEP→GLB conversion concern —
converter, cache, size caps — stays clear of the text-CAD routes in
`eda.py`, and so neither module grows past its budget. Mounted in
`main.py` with the same prefix, tags and member gate.

Route shape (the decision the spec left open): this is a **STEP-only**
GLB route. WRL is already a mesh format the browser reads natively
(three.js `VRMLLoader`), so the frontend fetches a `.wrl` straight from
`/api/eda/files/{ws}/{sha}.wrl` and never comes here; converting it
server-side would burn CPU and cache for no fidelity gain. SPICE is not
3D at all. So both non-STEP kinds answer 422 `eda.preview_unavailable`.

The conversion is CPU-heavy and prod runs one uvicorn worker, so it
hops onto the threadpool via `run_in_threadpool`; the cache in
`domain/eda/preview3d.py` means most requests never convert at all.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from app.core.deps import CurrentWorkspace, DbSession
from app.core.errors import ErrorCodes, raise_http
from app.core.ratelimit import limiter, workspace_key
from app.domain.eda import preview3d, storage
from app.domain.eda import service as eda_service
from app.domain.eda.models import EdaDatafile

router = APIRouter()

# A preview is a read the UI fires on every model selection; the on-disk
# cache absorbs the repeat, so this bucket exists mainly to bound the one
# expensive thing — a cold conversion — on the single prod worker.
_PREVIEW_RATE = "30/minute"

# Content-addressed by (source bytes + converter tag) and reachable only by
# the datafile's stable id, so the bytes behind a URL never change. A
# short private window absorbs a viewer's re-fetches without letting a
# shared cache hold one workspace's model for another. Mirrors the 2D
# preview stance in `eda.py`.
_CACHE_CONTROL = "private, max-age=300"


def _glb_response(path: str | None, data: bytes | None) -> Response:
    headers = {
        "Cache-Control": _CACHE_CONTROL,
        # The body is a binary model derived from a user upload, served
        # from our own origin — never let a browser sniff it into anything
        # else. Same stance as every other file this domain serves.
        "X-Content-Type-Options": "nosniff",
    }
    if path is not None:
        return FileResponse(path, media_type="model/gltf-binary", headers=headers)
    return Response(
        content=data or b"", media_type="model/gltf-binary", headers=headers
    )


@router.get("/datafiles/{datafile_id}/preview.glb")
@limiter.limit(_PREVIEW_RATE, key_func=workspace_key)
async def get_datafile_preview_glb(
    request: Request,
    datafile_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
) -> Response:
    """The datafile's STEP geometry, tessellated to a GLB three.js can draw.

    Archived datafiles preview too, for the same reason the 2D previews do
    (`eda.py::get_symbol_preview`): the restore decision needs to see what
    the entry is, and this surface is read-only.
    """
    row = eda_service.get_entry(db, ws=ws, Model=EdaDatafile, entry_id=datafile_id)
    if row.kind != "step":
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=ErrorCodes.EDA_PREVIEW_UNAVAILABLE,
            message=(
                "WRL models are rendered directly from the file and SPICE "
                "models are not 3D — only STEP has a GLB preview"
            ),
        )

    source_path = storage.path_for(ws.id, f"{row.sha256}.{storage.EXT_BY_KIND[row.kind]}")
    try:
        path, data = await run_in_threadpool(
            preview3d.get_or_build_glb, ws.id, row.sha256, source_path
        )
    except preview3d.SourceMissing:
        # A content-addressed source blob that isn't there is a "can't
        # happen"; answer loudly rather than with an empty model. Same
        # stance as `eda.py::_stored_bytes`.
        raise_http(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            code=ErrorCodes.EDA_PREVIEW_UNAVAILABLE,
            message="preview unavailable",
        )
    except (preview3d.SourceTooLarge, preview3d.OutputTooLarge):
        raise_http(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            code=ErrorCodes.EDA_FILE_TOO_LARGE,
            message="model is too large to preview",
        )
    except preview3d.ConversionFailed:
        # A corrupt or unconvertible STEP is a normal outcome here, not a
        # server fault — 422, never 500 (the whole reason the converter is
        # wrapped).
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=ErrorCodes.EDA_PREVIEW_UNAVAILABLE,
            message="could not build a 3D preview for this model",
        )
    return _glb_response(path, data)
