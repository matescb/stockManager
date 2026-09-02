"""Route mechanics shared by the EDA routers.

`eda.py` owns the library CRUD and the per-part config; `eda_import.py`
owns the vendor-zip and LCSC importers. Both mount under the same two
prefixes and both need the same three pieces of plumbing, so they live
here rather than being imported across route modules — the same split
`_parts_shared.py` made for the parts router family (#118).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import Request, UploadFile, status

from app.core.errors import ErrorCodes, raise_http
from app.domain.audit.service import log as _audit_log
from app.domain.eda import storage

__all__ = ["read_upload", "audit", "patch_comment"]


async def read_upload(file: UploadFile, *, kind: str) -> bytes:
    """Read an upload, bounded by the per-kind cap.

    Streaming-bounded read: take at most `cap + 1` bytes to detect
    over-cap, then reject. Starlette's UploadFile is spooled, so the
    extra byte stays in the spool — never lands in active memory beyond
    what we explicitly read here. Same shape as `attachments.py::upload`.
    """
    cap = storage.max_bytes_for(kind)
    contents = await file.read(cap + 1)
    if len(contents) > cap:
        raise_http(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            code=ErrorCodes.EDA_FILE_TOO_LARGE,
            message=f"{kind} upload exceeds {cap} bytes",
            max_bytes=cap,
        )
    if not contents:
        raise_http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=ErrorCodes.EDA_EMPTY_FILE,
            message="empty upload",
        )
    return contents


def audit(
    request: Request,
    db,
    ws,
    user,
    *,
    action: str,
    target_type: str,
    target_id: UUID,
    comment: str | None = None,
) -> None:
    _audit_log(
        db,
        ws=ws,
        user=user,
        action=action,
        target_type=target_type,
        target_ids=[target_id],
        comment=comment,
        request_id=getattr(request.state, "request_id", None),
    )


def patch_comment(payload) -> str:
    return "fields=" + ",".join(sorted(payload.model_fields_set))
