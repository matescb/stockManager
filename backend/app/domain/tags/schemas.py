"""Pydantic input schemas for the tags domain (#252).

Lifted out of `app/api/routes/tags.py` so every domain has one
canonical `domain/<x>/schemas.py`.

Every input schema keeps `model_config = ConfigDict(extra="forbid")` —
`tests/test_extra_forbid.py` regression-tests this and a silent drop
would let unknown fields through.
"""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict


__all__ = [
    "TagIn",
    "TagLinkIn",
]


class TagIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    color: str | None = None


class TagLinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag_id: UUID
    object_type: str
    object_id: UUID
