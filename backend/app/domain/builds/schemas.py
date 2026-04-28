from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BuildCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    project_id: UUID
    quantity: int = Field(gt=0, default=1)
    comments: str | None = None


class BuildPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    quantity: int | None = Field(default=None, gt=0)
    status: Literal["planned", "in_progress", "complete", "cancelled"] | None = None
    comments: str | None = None


class ConsumeLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_entry_id: UUID
    part_id: UUID  # may be the entry's main part or a registered substitute
    quantity: int = Field(gt=0)
    lot_id: UUID | None = None
    storage_location_id: UUID | None = None


class ConsumeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[ConsumeLineIn] = Field(min_length=1)
    output_storage_location_id: UUID | None = None
    output_lot_name: str | None = None
