"""Request/response schemas for `/api/label-templates`.

Ported from the sibling skladVA project (/mnt/data/WORK/sklad,
``backend/app/api/v1/label_templates.py`` — the ``ElementIn`` /
``TemplateCreate`` / ``TemplateUpdate`` / ``TemplateOut`` shapes), with the
entity-type literal pointing at THIS project's codeable set and ids as UUIDs.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.codes.models import CodeEntityType

# Bound the element list so a crafted body cannot push an unbounded JSONB blob
# into the row (and, through it, into every rendered job).
MAX_ELEMENTS = 100

# Text inside an element. Generous enough for a description line, small enough
# that a hundred of them stays a sane request body.
MAX_ELEMENT_TEXT = 2000

LabelEntityType = CodeEntityType

# Guard rails on geometry. Neither bound is a printer limit — they exist so a
# fat-fingered value produces a 422 instead of a job the printer chews on.
_MM_MAX = 500.0


class ElementIn(BaseModel):
    """One placed element.

    ``extra="allow"`` on purpose: each kind carries its own knobs (``ec`` and
    ``dotsize_mm`` for a QR, ``bc_type``/``height_mm``/``ne_mm`` for a barcode,
    ``size_pt``/``font`` for text) and the renderer reads them defensively with
    per-field defaults. Enumerating all of them here would mean editing two
    files to add a knob, and the renderer already treats anything it does not
    recognise as absent. What IS validated here is the part the renderer cannot
    recover from: the ``kind``, and the shared placement fields.
    """

    model_config = ConfigDict(extra="allow")

    kind: str
    x_mm: float = Field(default=0.0, ge=-_MM_MAX, le=_MM_MAX)
    y_mm: float = Field(default=0.0, ge=-_MM_MAX, le=_MM_MAX)
    rotation: int = Field(default=0, ge=0, lt=360)
    text: str | None = Field(default=None, max_length=MAX_ELEMENT_TEXT)
    binding: str | None = Field(default=None, max_length=64)


class TemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    entity_type: LabelEntityType
    width_mm: float = Field(default=50.0, gt=0, le=_MM_MAX)
    height_mm: float = Field(default=30.0, gt=0, le=_MM_MAX)
    gap_mm: float = Field(default=3.0, ge=0, le=50)
    heat: int = Field(default=100, ge=0, le=200)
    speed: int = Field(default=0, ge=0, le=400)
    method: Literal["T", "D"] = "T"
    dpi: int = Field(default=300, ge=100, le=1200)
    is_default: bool = False
    elements: list[ElementIn] = Field(default_factory=list, max_length=MAX_ELEMENTS)


class TemplateUpdate(BaseModel):
    """Partial update. ``entity_type`` is absent deliberately — retargeting a
    template at another entity type would silently invalidate every binding it
    places, and would have to race the default index. Make a new template."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    width_mm: float | None = Field(default=None, gt=0, le=_MM_MAX)
    height_mm: float | None = Field(default=None, gt=0, le=_MM_MAX)
    gap_mm: float | None = Field(default=None, ge=0, le=50)
    heat: int | None = Field(default=None, ge=0, le=200)
    speed: int | None = Field(default=None, ge=0, le=400)
    method: Literal["T", "D"] | None = None
    dpi: int | None = Field(default=None, ge=100, le=1200)
    is_default: bool | None = None
    elements: list[ElementIn] | None = Field(default=None, max_length=MAX_ELEMENTS)


class TemplateOut(BaseModel):
    id: UUID
    name: str
    entity_type: str
    width_mm: float
    height_mm: float
    gap_mm: float
    heat: int
    speed: int
    method: str
    dpi: int
    is_default: bool
    elements: list[dict[str, Any]]


class TestPrintIn(BaseModel):
    """Body for ``POST /{id}/test-print``.

    With no ``entity_id`` the label is rendered from sample data — the
    "does my layout look right on real stock?" case. With one, the label is
    rendered for that object, which mints its object code (get-or-create)
    exactly as ``POST /api/codes`` would.
    """

    entity_id: UUID | None = None
    copies: int = Field(default=1, ge=1, le=20)


class TestPrintOut(BaseModel):
    print_job_id: UUID
    status: str
    code: str | None = None


class RenderOut(BaseModel):
    jscript: str


__all__ = [
    "ElementIn",
    "LabelEntityType",
    "MAX_ELEMENTS",
    "RenderOut",
    "TemplateCreate",
    "TemplateOut",
    "TemplateUpdate",
    "TestPrintIn",
    "TestPrintOut",
]
