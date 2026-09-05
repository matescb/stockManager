"""The built-in default label layout for each codeable entity type.

Adapted from the sibling skladVA project's seeding script (/mnt/data/WORK/sklad,
``backend/scripts/seed_label_templates.py``). skladVA is single-tenant, so a
one-shot script writing global rows was enough. Here templates are
workspace-scoped, so the catalog lives in Python as the single source of truth
and ``template_service.ensure_defaults`` materialises it into a workspace
on demand (idempotently) — the same lazy-materialisation shape #892 chose for
object codes, for the same reason: most of these rows are only wanted by the
workspaces that actually print.

Deliberately NOT a migration data-backfill: the catalog would then exist twice
(here and frozen in SQL) and drift on the first tweak, and a workspace created
after the migration would get nothing.

Layout
------
Every default is the same shape, sized to the stock: a QR carrying ``{{url}}``
(the scan-to-open link) on the left, the human-readable ``{{code}}`` beneath it
so a smudged label can still be typed back in, and the object's name — plus one
type-specific line — to its right. Numbers are millimetres from the label's
top-left corner.
"""

from __future__ import annotations

from typing import Any, TypedDict


class DefaultTemplateSpec(TypedDict):
    """One built-in template: the row fields ``ensure_defaults`` inserts."""

    name: str
    width_mm: float
    height_mm: float
    gap_mm: float
    heat: int
    speed: int
    method: str
    dpi: int
    elements: list[dict[str, Any]]


# Standard stock for these defaults: 50 x 30 mm die-cut with a 3 mm gap, the
# common small-parts label. An operator on different stock edits the geometry
# (or creates their own template); nothing downstream assumes these numbers.
_W = 50.0
_H = 30.0
_GAP = 3.0

# Shared geometry of the QR block: a 20 mm square symbol in the left column
# with the code typeset directly under it.
_QR_X = 2.0
_QR_Y = 2.0
_QR_DOTSIZE = 0.5
_CODE_Y = 23.0
_TEXT_X = 25.0


def _qr() -> dict[str, Any]:
    """The scan target. No explicit binding: ``qr`` defaults to ``{{url}}``."""
    return {
        "kind": "qr",
        "x_mm": _QR_X,
        "y_mm": _QR_Y,
        "dotsize_mm": _QR_DOTSIZE,
        "ec": "M",
    }


def _code_caption() -> dict[str, Any]:
    """The human-transcribable fallback for when the QR will not scan."""
    return {
        "kind": "text",
        "x_mm": _QR_X,
        "y_mm": _CODE_Y,
        "binding": "code",
        "font": 5,  # Swiss 721 Bold — this is the line people read aloud.
        "size_pt": 9,
    }


def _line(y_mm: float, binding: str, *, size_pt: int = 8, font: int = 3) -> dict[str, Any]:
    return {
        "kind": "text",
        "x_mm": _TEXT_X,
        "y_mm": y_mm,
        "binding": binding,
        "font": font,
        "size_pt": size_pt,
    }


def _spec(name: str, extra_lines: list[dict[str, Any]]) -> DefaultTemplateSpec:
    return DefaultTemplateSpec(
        name=name,
        width_mm=_W,
        height_mm=_H,
        gap_mm=_GAP,
        heat=100,
        speed=0,
        method="T",
        dpi=300,
        elements=[
            _qr(),
            _code_caption(),
            _line(3.0, "name", size_pt=9, font=5),
            *extra_lines,
        ],
    )


# One built-in per codeable entity type. Keys MUST stay in step with
# ``models.LABEL_ENTITY_TYPES`` — `test_label_templates.py` pins that.
BUILT_IN_TEMPLATES: dict[str, DefaultTemplateSpec] = {
    "part": _spec(
        "Part label (default)",
        [_line(10.0, "mpn"), _line(15.0, "manufacturer")],
    ),
    "lot": _spec(
        "Lot label (default)",
        [_line(10.0, "part_name"), _line(15.0, "serial")],
    ),
    "storage_location": _spec(
        "Storage location label (default)",
        [_line(10.0, "description")],
    ),
    "order": _spec(
        "Order label (default)",
        [_line(10.0, "supplier"), _line(15.0, "status")],
    ),
    "build": _spec(
        "Build label (default)",
        [_line(10.0, "project_name"), _line(15.0, "quantity")],
    ),
}


__all__ = ["BUILT_IN_TEMPLATES", "DefaultTemplateSpec"]
