from __future__ import annotations

import os
import uuid as uuidlib
from uuid import UUID

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select

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
    storage_key = f"{ws.id}/{uuidlib.uuid4()}-{file.filename}"
    abs_path = os.path.join(settings().UPLOAD_DIR, storage_key)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    contents = await file.read()
    with open(abs_path, "wb") as f:
        f.write(contents)
    a = Attachment(
        workspace_id=ws.id,
        object_type=object_type,
        object_id=object_id,
        file_name=file.filename or "upload",
        file_type=file_type,
        mime_type=file.content_type,
        size_bytes=len(contents),
        storage_key=storage_key,
        uploaded_by=user.id,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(a)
    db.commit()
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
    a = db.get(Attachment, attachment_id)
    if not a or a.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="attachment not found")
    abs_path = os.path.join(settings().UPLOAD_DIR, a.storage_key)
    return FileResponse(abs_path, media_type=a.mime_type or "application/octet-stream", filename=a.file_name)


@router.delete("/{attachment_id}")
def delete(attachment_id: UUID, db: DbSession, ws: CurrentWorkspace):
    a = db.get(Attachment, attachment_id)
    if not a or a.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="attachment not found")
    abs_path = os.path.join(settings().UPLOAD_DIR, a.storage_key)
    try:
        os.remove(abs_path)
    except FileNotFoundError:
        pass
    db.delete(a)
    db.commit()
    return ok(None, "deleted")
