"""Pydantic input schemas for the storage domain (#252).

Lifted out of `app/api/routes/storage.py` so every domain has one
canonical `domain/<x>/schemas.py`.

Every input schema keeps `model_config = ConfigDict(extra="forbid")` —
`tests/test_extra_forbid.py` regression-tests this and a silent drop
would let unknown fields through.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "StorageIn",
    "StoragePatch",
]


class StorageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    single_part_only: bool = False
    existing_parts_only: bool = False
    is_full: bool = False


class StoragePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    single_part_only: bool | None = None
    existing_parts_only: bool | None = None
    is_full: bool | None = None
