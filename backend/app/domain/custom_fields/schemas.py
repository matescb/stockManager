"""Pydantic input schemas for the custom_fields domain (#252).

Lifted out of `app/api/routes/custom_fields.py` so every domain has
one canonical `domain/<x>/schemas.py`.

Every input schema keeps `model_config = ConfigDict(extra="forbid")` —
`tests/test_extra_forbid.py` regression-tests this and a silent drop
would let unknown fields through.
"""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CustomFieldIn",
]


class CustomFieldIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_type: str
    object_id: UUID
    key: str = Field(min_length=1, max_length=256)
    value: str | None = Field(default=None, max_length=1024)
