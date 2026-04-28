from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class BuildCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    project_id: UUID
    quantity: int = Field(gt=0, default=1)
    comments: str | None = None


class BuildPatchIn(BaseModel):
    name: str | None = None
    quantity: int | None = Field(default=None, gt=0)
    status: Literal["planned", "in_progress", "complete", "cancelled"] | None = None
    comments: str | None = None


class ConsumeLineIn(BaseModel):
    project_entry_id: UUID
    part_id: UUID  # may be the entry's main part or a registered substitute
    quantity: int = Field(gt=0)
    lot_id: UUID | None = None
    storage_location_id: UUID | None = None


class ConsumeIn(BaseModel):
    lines: list[ConsumeLineIn] = Field(min_length=1)
    output_storage_location_id: UUID | None = None
    output_lot_name: str | None = None
