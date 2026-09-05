"""Label-template persistence + binding-context assembly.

The DB-and-settings half of label rendering. :mod:`label_render` is pure; this
module is where the workspace filter, the object-code mint and the scan URL
live, so the renderer never has to reach for any of them.

Ported in shape from the sibling skladVA project (/mnt/data/WORK/sklad,
``backend/app/api/v1/label_templates.py`` — its ``_clear_existing_default`` /
``_load_or_404`` / sample-context helpers, and
``backend/scripts/seed_label_templates.py`` for the defaults), with the
single-tenant assumptions removed: every query here filters by ``ws.id`` and
"one default per entity_type" becomes "one default per (workspace,
entity_type)".

Writes ``db.flush()``; the ``get_db`` dependency owns the commit.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api._helpers import assert_in_workspace, assert_polymorphic_in_workspace
from app.core.config import settings
from app.domain.codes import service as codes_service
from app.domain.codes.models import ObjectCode
from app.domain.parts.models import Part
from app.domain.printing.default_templates import BUILT_IN_TEMPLATES
from app.domain.printing.models import LabelTemplate
from app.domain.projects.models import Project
from app.domain.workspaces.models import Workspace

# The sample code shown by the debug render endpoint. Deliberately a
# fixed, obviously-fake string in the Crockford alphabet — a sample render
# must not mint a real code, and a real code must never be mistaken for this.
SAMPLE_CODE = "SAMPLE00"


# ---------------------------------------------------------------------------
# Queries — every one scoped to the workspace
# ---------------------------------------------------------------------------


def list_templates(
    db: Session, *, ws: Workspace, entity_type: str | None = None
) -> list[LabelTemplate]:
    """All templates in this workspace, newest-default-first within a type."""
    stmt = select(LabelTemplate).where(LabelTemplate.workspace_id == ws.id)
    if entity_type is not None:
        stmt = stmt.where(LabelTemplate.entity_type == entity_type)
    stmt = stmt.order_by(
        LabelTemplate.entity_type,
        LabelTemplate.is_default.desc(),
        LabelTemplate.name,
    )
    return list(db.execute(stmt).scalars())


def get_template(db: Session, *, ws: Workspace, template_id: UUID) -> LabelTemplate:
    """One template by id, or 404. Cross-workspace ids are 404, never 403."""
    return assert_in_workspace(
        db, LabelTemplate, template_id, ws.id, label="label template"
    )


def clear_existing_default(
    db: Session, *, ws: Workspace, entity_type: str, exclude_id: UUID | None = None
) -> None:
    """Demote the current default for ``entity_type`` in this workspace.

    Run BEFORE promoting another template, inside the same transaction: the
    partial unique index ``(workspace_id, entity_type) WHERE is_default`` would
    otherwise reject the second default with an IntegrityError the caller has
    no clean way to report.
    """
    stmt = select(LabelTemplate).where(
        LabelTemplate.workspace_id == ws.id,
        LabelTemplate.entity_type == entity_type,
        LabelTemplate.is_default.is_(True),
    )
    for current in db.execute(stmt).scalars():
        if exclude_id is not None and current.id == exclude_id:
            continue
        current.is_default = False
    db.flush()


def ensure_defaults(
    db: Session, *, ws: Workspace
) -> tuple[list[LabelTemplate], list[str]]:
    """Materialise the built-in default templates missing from this workspace.

    Idempotent get-or-create, per entity type: a type that already has a
    default is left exactly as the operator edited it. Returns
    ``(all_templates, created_entity_types)`` so the caller can decide whether
    the call was a mutation worth auditing.
    """
    existing_defaults = {
        row.entity_type
        for row in db.execute(
            select(LabelTemplate).where(
                LabelTemplate.workspace_id == ws.id,
                LabelTemplate.is_default.is_(True),
            )
        ).scalars()
    }

    created: list[str] = []
    for entity_type, spec in BUILT_IN_TEMPLATES.items():
        if entity_type in existing_defaults:
            continue
        db.add(
            LabelTemplate(
                workspace_id=ws.id,
                entity_type=entity_type,
                name=spec["name"],
                width_mm=spec["width_mm"],
                height_mm=spec["height_mm"],
                gap_mm=spec["gap_mm"],
                heat=spec["heat"],
                speed=spec["speed"],
                method=spec["method"],
                dpi=spec["dpi"],
                is_default=True,
                elements=list(spec["elements"]),
            )
        )
        created.append(entity_type)
    if created:
        db.flush()
    return list_templates(db, ws=ws), created


# ---------------------------------------------------------------------------
# Binding context
# ---------------------------------------------------------------------------


def scan_url(code: str) -> str:
    """The scan-to-open URL a label's QR encodes.

    ``{APP_BASE_URL}/c/{code}`` — the frontend route #892 added. There is
    exactly one code namespace in this app, so there is exactly one URL shape;
    do not introduce a second.
    """
    base = (settings().APP_BASE_URL or "").rstrip("/")
    return f"{base}/c/{code}"


def _base_context(entity_type: str, code: str, *, workspace_name: str) -> dict[str, str]:
    """The bindings every entity type has."""
    return {
        "code": code,
        "url": scan_url(code),
        "entity_type": entity_type,
        "workspace": workspace_name,
        # Declared so an unfilled type-specific binding renders empty rather
        # than leaving the raw ``{{token}}`` visible on the label.
        "name": "",
        "description": "",
        "mpn": "",
        "manufacturer": "",
        "part_name": "",
        "serial": "",
        "supplier": "",
        "status": "",
        "project_name": "",
        "quantity": "",
    }


def _str(value: Any) -> str:
    return "" if value is None else str(value)


def _entity_fields(db: Session, ws: Workspace, entity_type: str, row: Any) -> dict[str, str]:
    """The type-specific bindings for one already-workspace-checked row.

    Every follow-on lookup (a lot's part, a build's project) re-filters on
    ``ws.id``: ``row`` is proven to be ours, but a stale FK is not proof about
    its target, and this is a read that ends up printed on a label.
    """
    fields: dict[str, str] = {"name": _str(getattr(row, "name", None))}

    if entity_type == "part":
        fields["mpn"] = _str(row.mpn)
        fields["manufacturer"] = _str(row.manufacturer)
        fields["description"] = _str(row.description)
    elif entity_type == "lot":
        fields["serial"] = _str(row.serial_number)
        fields["description"] = _str(row.description)
        if row.part_id is not None:
            part = db.execute(
                select(Part).where(Part.id == row.part_id, Part.workspace_id == ws.id)
            ).scalar_one_or_none()
            if part is not None:
                fields["part_name"] = _str(part.name)
                fields["mpn"] = _str(part.mpn)
                fields["manufacturer"] = _str(part.manufacturer)
    elif entity_type == "storage_location":
        fields["description"] = _str(row.description)
    elif entity_type == "order":
        fields["supplier"] = _str(row.supplier)
        fields["status"] = _str(row.status)
    elif entity_type == "build":
        fields["status"] = _str(row.status)
        fields["quantity"] = _str(row.quantity)
        project = db.execute(
            select(Project).where(
                Project.id == row.project_id, Project.workspace_id == ws.id
            )
        ).scalar_one_or_none()
        if project is not None:
            fields["project_name"] = _str(project.name)

    return fields


def sample_context(entity_type: str, *, ws: Workspace) -> dict[str, str]:
    """A realistic but entirely fabricated context, for the debug render.

    Touches no rows and mints no code, so an operator can preview a layout
    before anything in the workspace has ever been labelled.
    """
    context = _base_context(entity_type, SAMPLE_CODE, workspace_name=ws.name)
    context.update(
        {
            "name": "Sample " + entity_type.replace("_", " "),
            "description": "Sample description",
            "mpn": "SMPL-1234",
            "manufacturer": "Sample Mfr",
            "part_name": "Sample part",
            "serial": "SN-0001",
            "supplier": "Sample Supplier",
            "status": "draft",
            "project_name": "Sample project",
            "quantity": "10",
        }
    )
    return context


def context_for_entity(
    db: Session, *, ws: Workspace, entity_type: str, entity_id: UUID
) -> tuple[dict[str, str], ObjectCode, bool]:
    """Build the real binding context for one object, minting its code.

    ``{{code}}`` comes from the #892 object-codes service (mint-or-get) — there
    is one code system in this app and this is it. ``mint_or_get`` does the
    existence + workspace check on ``entity_id`` itself, so a foreign UUID is a
    404 before anything is read or minted.

    Returns ``(context, code_row, code_created)``; the flag lets the caller
    audit the mint the same way ``POST /api/codes`` does.
    """
    code_row, created = codes_service.mint_or_get(
        db, ws=ws, entity_type=entity_type, entity_id=entity_id
    )
    # Re-resolve the row itself (mint_or_get validates but does not return it).
    row = assert_polymorphic_in_workspace(db, entity_type, entity_id, ws.id)

    context = _base_context(entity_type, code_row.code, workspace_name=ws.name)
    context.update(_entity_fields(db, ws, entity_type, row))
    return context, code_row, created


__all__ = [
    "SAMPLE_CODE",
    "clear_existing_default",
    "context_for_entity",
    "ensure_defaults",
    "get_template",
    "list_templates",
    "sample_context",
    "scan_url",
]
