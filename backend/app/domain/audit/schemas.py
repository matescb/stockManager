"""Pydantic read schemas for the audit domain (BE2-024)."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    user_id: UUID | None
    action: str
    target_type: str | None
    target_ids: list[UUID] | None
    comment: str | None
    request_id: str | None
    created_at: datetime
