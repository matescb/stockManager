"""Pydantic input schemas for the lots domain (#252).

Lifted out of `app/api/routes/lots.py` so every domain has one
canonical `domain/<x>/schemas.py`.

Every input schema keeps `model_config = ConfigDict(extra="forbid")` —
`tests/test_extra_forbid.py` regression-tests this and a silent drop
would let unknown fields through.
"""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict

__all__ = [
    "LotPatch",
    "LotAdjustIn",
]


class LotPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    comments: str | None = None
    expiration_date: str | None = None
    serial_number: str | None = None


class LotAdjustIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actual_quantity: int
    storage_location_id: UUID | None = None
    comments: str | None = None
