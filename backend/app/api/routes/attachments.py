from __future__ import annotations

import os
import re
import uuid as uuidlib
from uuid import UUID

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api._helpers import assert_in_workspace, assert_polymorphic_in_workspace
from app.core.config import settings
from app.core.deps import (
    CurrentUser,
    CurrentWorkspace,
    DbSession,
    get_current_user,
    get_current_workspace,
)
from app.core.responses import ok
from app.domain.attachments.models import Attachment
from app.infra.db import get_db

router = APIRouter()


# Allow-list of MIME types we accept on upload, with the canonical
# extension we use for the stored filename. SVG is intentionally excluded
# — sanitising it correctly is a research project, and the only place we
# render inline is the browser (no scoped iframe / sandbox), so any
# escape becomes a same-origin XSS. PNG / JPEG / WebP / PDF cover every
# real use case here (part photos, datasheets, lot images).
_ALLOWED_MIMES: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "application/pdf": "pdf",
}


def _detect_mime(header: bytes) -> str | None:
    """Return the canonical MIME if `header` matches one of the allowed
    formats, else None. Magic-byte sniff defeats the `evil.html declared
    image/png` upload — the declared Content-Type is never trusted on
    its own.

    Reference signatures:
    - PNG  89 50 4E 47 0D 0A 1A 0A
    - JPEG FF D8 FF
    - WebP "RIFF" at 0..3, "WEBP" at 8..11 (variable-length size in 4..7)
    - PDF  25 50 44 46 2D ("%PDF-")
    """
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    if header.startswith(b"%PDF-"):
        return "application/pdf"
    return None


_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(name: str | None, mime: str) -> str:
    """Sanitise the user-supplied filename for storage and Content-
    Disposition. The extension is always derived from the validated MIME
    so a `.html` suffix on a PNG can never make it back to the browser.

    - Strip any client-supplied extension (we replace it with the canonical one).
    - Replace anything outside [A-Za-z0-9._-] with `_`.
    - Cap at 80 chars (matches the existing `withDownloadName` helper on
      the FE — keeps Save-As dialogs sane).
    - Empty / all-stripped → `attachment-<8-hex>.<ext>`.
    """
    ext = _ALLOWED_MIMES[mime]
    base = (name or "").rsplit(".", 1)[0]
    base = _FILENAME_RE.sub("_", base).strip("._-")
    if not base:
        base = f"attachment-{uuidlib.uuid4().hex[:8]}"
    base = base[:80]
    return f"{base}.{ext}"


def _serialize(a: Attachment) -> dict:
    return {
        "id": str(a.id),
        "object_type": a.object_type,
        "object_id": str(a.object_id),
        "file_name": a.file_name,
        "file_type": a.file_type,
        "mime_type": a.mime_type,
        "size_bytes": a.size_bytes,
        "created_at": a.created_at.isoformat(),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload(
    object_type: str = Form(...),
    object_id: UUID = Form(...),
    file_type: str = Form("other"),
    file: UploadFile = File(...),
    db=Depends(get_db),
    ws=Depends(get_current_workspace),
    user=Depends(get_current_user),
):
    # Caller must own (or be a member of) the workspace AND the polymorphic
    # target object must exist in this workspace. Stops cross-tenant writes
    # before any disk I/O happens.
    assert_polymorphic_in_workspace(db, object_type, object_id, ws.id)

    # Streaming-bounded read: read at most MAX_UPLOAD_BYTES + 1 to detect
    # over-cap, then reject. Starlette's UploadFile is spooled, so the
    # extra byte stays in the spool — never lands in active memory beyond
    # what we explicitly read here.
    max_bytes = settings().MAX_UPLOAD_BYTES
    contents = await file.read(max_bytes + 1)
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"upload exceeds {max_bytes} bytes",
        )
    if not contents:
        raise HTTPException(status_code=400, detail="empty upload")

    # Validate the bytes against the allow-list and confirm the declared
    # Content-Type matches reality. The declared MIME is never trusted on
    # its own — `evil.html` with `Content-Type: image/png` would pass the
    # allow-list check but fail the magic-byte check.
    actual_mime = _detect_mime(contents[:16])
    if actual_mime is None:
        raise HTTPException(
            status_code=415,
            detail="unsupported file type — allowed: PNG, JPEG, WebP, PDF",
        )
    if file.content_type and file.content_type != actual_mime:
        raise HTTPException(
            status_code=415,
            detail=(
                f"declared content-type ({file.content_type}) does not match "
                f"actual content ({actual_mime})"
            ),
        )

    safe_name = _safe_filename(file.filename, actual_mime)
    storage_key = f"{ws.id}/{uuidlib.uuid4()}-{safe_name}"
    abs_path = os.path.join(settings().UPLOAD_DIR, storage_key)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(contents)
    a = Attachment(
        workspace_id=ws.id,
        object_type=object_type,
        object_id=object_id,
        # Store the SANITIZED name + DETECTED MIME — never echo back what
        # the client sent. Subsequent downloads serve the sanitized form.
        file_name=safe_name,
        file_type=file_type,
        mime_type=actual_mime,
        size_bytes=len(contents),
        storage_key=storage_key,
        uploaded_by=user.id,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(a)
    db.flush()
    return ok(_serialize(a))


@router.get("/by-object/{object_type}/{object_id}")
def list_for_object(object_type: str, object_id: UUID, db: DbSession, ws: CurrentWorkspace):
    rows = list(
        db.execute(
            select(Attachment)
            .where(Attachment.workspace_id == ws.id)
            .where(Attachment.object_type == object_type)
            .where(Attachment.object_id == object_id)
            .order_by(Attachment.created_at.desc())
        ).scalars()
    )
    return ok([_serialize(a) for a in rows])


@router.get("/{attachment_id}/download")
def download(attachment_id: UUID, db: DbSession, ws: CurrentWorkspace):
    a = assert_in_workspace(db, Attachment, attachment_id, ws.id, label="attachment")
    abs_path = os.path.join(settings().UPLOAD_DIR, a.storage_key)
    # Legacy attachments (uploaded before this PR's allow-list landed) may
    # carry NULL or non-allow-listed mime_types. Serve them as
    # `application/octet-stream` so the browser cannot inline-render an
    # `evil.svg` or `evil.html` from before the allow-list. New uploads
    # have a canonical MIME — the conditional collapses for them.
    served_mime = (
        a.mime_type
        if a.mime_type in _ALLOWED_MIMES
        else "application/octet-stream"
    )
    # Force Content-Disposition: attachment regardless of MIME — even an
    # allow-listed image won't render inline this way. Pre-empts any
    # future MIME-allow-list expansion accidentally re-introducing the
    # inline-render vector.
    return FileResponse(
        abs_path,
        media_type=served_mime,
        filename=a.file_name,
        content_disposition_type="attachment",
    )


@router.delete("/{attachment_id}")
def delete(attachment_id: UUID, db: DbSession, ws: CurrentWorkspace):
    a = assert_in_workspace(db, Attachment, attachment_id, ws.id, label="attachment")
    abs_path = os.path.join(settings().UPLOAD_DIR, a.storage_key)
    try:
        os.remove(abs_path)
    except FileNotFoundError:
        pass
    db.delete(a)
    return ok(None, "deleted")
