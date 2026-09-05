"""Pydantic shapes for the object-code API.

`ObjectCodeIn` keeps `extra="forbid"` like every other input schema in
this codebase, so a typo'd field is a 422 rather than a silently ignored
key.
"""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.codes.models import CodeEntityType

__all__ = ["ObjectCodeIn", "ObjectCodeOut"]


class ObjectCodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # `Literal` over the closed set: an unknown entity_type is a 422 with
    # the permitted values spelled out, before any DB work happens.
    entity_type: CodeEntityType
    entity_id: UUID


class ObjectCodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    entity_type: str
    entity_id: UUID
