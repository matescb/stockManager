from __future__ import annotations

from decimal import Decimal
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


# --- Multi-stage builds (Track B2) ------------------------------------------


class BuildStageLineIn(BaseModel):
    """One BOM line a stage consumes, and how much of it.

    `portion_pct` is a percentage of the line's whole-build requirement (the
    attrition-adjusted integer from `service.py::_required`), not of
    `project_entries.quantity` — so attrition applies exactly once, in the
    one place it always has.
    """

    model_config = ConfigDict(extra="forbid")

    project_entry_id: UUID
    portion_pct: Decimal = Field(default=Decimal(100), gt=0, le=100, max_digits=7, decimal_places=4)


class BuildStageCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    # Consumption order. Defaults to "append after the current last stage".
    sequence: int | None = Field(default=None, ge=0)
    comments: str | None = None
    lines: list[BuildStageLineIn] = Field(min_length=1)


class KitIn(BaseModel):
    """Kitting (Track B3) — consolidate a build's components onto one tray.

    The staging location is a request parameter rather than a column on
    `builds`: which tray is free is a property of today's shop floor, not
    of the build, and the same build may legitimately be kitted onto a
    different location on a re-kit. See `domain/builds/kitting.py`.
    """

    model_config = ConfigDict(extra="forbid")

    storage_location_id: UUID


class StageConsumeIn(BaseModel):
    """Per-stage consume payload.

    Identical to `ConsumeIn` except the output fields only take effect on the
    stage that completes the build — a staged build produces its sub-assembly
    lot once, not once per stage.
    """

    model_config = ConfigDict(extra="forbid")

    lines: list[ConsumeLineIn] = Field(min_length=1)
    output_storage_location_id: UUID | None = None
    output_lot_name: str | None = None
