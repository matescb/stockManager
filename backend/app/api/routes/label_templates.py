"""Label templates — `/api/label-templates`.

Workspace-scoped CRUD over :class:`~app.domain.printing.models.LabelTemplate`,
plus the two operational endpoints that turn a stored layout into something
physical:

  GET    /api/label-templates?entity_type=      list (optional type filter)
  POST   /api/label-templates                   create
  POST   /api/label-templates/defaults          seed the built-in defaults
  GET    /api/label-templates/{id}              fetch one
  PATCH  /api/label-templates/{id}              partial update
  DELETE /api/label-templates/{id}              delete
  GET    /api/label-templates/{id}/jscript      rendered JScript (sample, debug)
  POST   /api/label-templates/{id}/test-print   render + ship to the printer

Thin routes: the queries, the default-swap and the binding context live in
`app/domain/printing/template_service.py`, and the JScript itself in
`app/domain/printing/label_render.py`.

Router shape ported from the sibling skladVA project (/mnt/data/WORK/sklad,
`backend/app/api/v1/label_templates.py`), with its single-tenant assumptions
replaced by this codebase's workspace scoping and role model.

Auth
----
Reads are member-gated at the mount (`_member_gate` in `main.py`). MUTATIONS
are admin+: a template is shared infrastructure — one row decides what every
label in the workspace looks like, and `test-print` drives physical hardware —
so it is an operator task, not a member one. Same call skladVA made with
`require_admin`.

That admin check is expressed two different ways, and the difference is
load-bearing (BE2-009). The collection routes (`POST ""`, `POST /defaults`)
name no resource, so they carry `Depends(require_role("admin"))` at the route.
The `/{template_id}` mutations must NOT: a route-level dependency runs BEFORE
the handler, so a non-admin probing another workspace's template id would get
403 — an oracle saying "this id exists somewhere, you just lack the role".
They use `_helpers.require_resource_access` instead, which resolves existence
and workspace membership first (404 for both) and only then checks the role.

Why test-print returns a response instead of raising on printer failure
-----------------------------------------------------------------------
`get_db` rolls back the request transaction on ANY raised exception. A printer
failure marks the `print_jobs` row `failed` — and raising a 409 would roll that
row straight back out, destroying the record the operator is told to inspect.
So the printer-failure path RETURNS a 409 `JSONResponse` built with
`responses.err()`: the body is byte-identical to what `raise_http` would have
produced, but the transaction exits cleanly and the failed job (and its audit
row) survive. Do not "tidy" this into a `raise_http`.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse

from app.api._helpers import require_resource_access
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession, require_role
from app.core.errors import ErrorCodes, raise_http
from app.core.logging import get_logger
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import Envelope, err, ok
from app.domain.audit.service import log as _audit_log
from app.domain.printing import label_render, print_service, template_service
from app.domain.printing.models import ELEMENT_KINDS, LabelTemplate
from app.domain.printing.schemas import (
    ElementIn,
    LabelEntityType,
    RenderOut,
    TemplateCreate,
    TemplateOut,
    TemplateUpdate,
    TestPrintIn,
    TestPrintOut,
)

router = APIRouter()

_log = get_logger(__name__)

# Collection-route admin gate. Only for routes that name NO resource — see
# the module docstring on why `/{template_id}` must not use this.
_admin_gate = [Depends(require_role("admin"))]


def _admin_template(
    db: DbSession, ws: CurrentWorkspace, user: CurrentUser, template_id: UUID
) -> LabelTemplate:
    """Resolve a template for an admin-only mutation, in the safe order.

    Existence + workspace first (both 404), role second (403). Never swap
    those — see the module docstring.
    """
    return require_resource_access(
        db,
        LabelTemplate,
        template_id,
        ws=ws,
        user=user,
        role="admin",
        label="label template",
    )

# Scalar columns a PATCH may set directly. `elements` and `is_default` need
# extra handling and are applied separately.
_PATCHABLE = (
    "name",
    "width_mm",
    "height_mm",
    "gap_mm",
    "heat",
    "speed",
    "method",
    "dpi",
)


def _to_out(row: LabelTemplate) -> TemplateOut:
    return TemplateOut(
        id=row.id,
        name=row.name,
        entity_type=row.entity_type,
        width_mm=float(row.width_mm),
        height_mm=float(row.height_mm),
        gap_mm=float(row.gap_mm),
        heat=row.heat,
        speed=row.speed,
        method=row.method,
        dpi=row.dpi,
        is_default=row.is_default,
        elements=list(row.elements or []),
    )


def _validate_elements(elements: list[ElementIn]) -> list[dict[str, Any]]:
    """Check every element kind and serialise to plain dicts for JSONB.

    The renderer skips a kind it does not know, so an unvalidated blob would
    store a template that silently prints a blank label. Rejecting on write is
    where the operator can still do something about it.
    """
    out: list[dict[str, Any]] = []
    for element in elements:
        if element.kind not in ELEMENT_KINDS:
            raise_http(
                status.HTTP_400_BAD_REQUEST,
                ErrorCodes.LABEL_TEMPLATE_INVALID,
                f"unknown element kind {element.kind!r}; "
                f"allowed: {', '.join(ELEMENT_KINDS)}",
            )
        out.append(element.model_dump(exclude_none=True))
    return out


def _render_or_400(row: LabelTemplate, context: dict[str, str], *, copies: int = 1) -> str:
    try:
        return label_render.render(row, context, copies=copies)
    except label_render.LabelRenderError as exc:
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            ErrorCodes.LABEL_TEMPLATE_INVALID,
            f"could not render template: {exc}",
        )


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("")
def list_templates(
    db: DbSession,
    ws: CurrentWorkspace,
    entity_type: Annotated[LabelEntityType | None, Query()] = None,
) -> Envelope[list[TemplateOut]]:
    """List this workspace's label templates, newest default first per type."""
    rows = template_service.list_templates(db, ws=ws, entity_type=entity_type)
    return ok([_to_out(row) for row in rows])


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=_admin_gate)
def create_template(
    request: Request,
    payload: TemplateCreate,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[TemplateOut]:
    """Create a template. Setting `is_default` atomically demotes the incumbent
    for the same entity type, so the partial unique index is never violated."""
    elements = _validate_elements(payload.elements)
    if payload.is_default:
        template_service.clear_existing_default(
            db, ws=ws, entity_type=payload.entity_type
        )

    row = LabelTemplate(
        workspace_id=ws.id,
        created_by=user.id,
        updated_by=user.id,
        name=payload.name,
        entity_type=payload.entity_type,
        width_mm=payload.width_mm,
        height_mm=payload.height_mm,
        gap_mm=payload.gap_mm,
        heat=payload.heat,
        speed=payload.speed,
        method=payload.method,
        dpi=payload.dpi,
        is_default=payload.is_default,
        elements=elements,
    )
    db.add(row)
    db.flush()
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="label_template.created",
        target_type="label_template",
        target_ids=[row.id],
        comment=f"entity_type={row.entity_type} is_default={row.is_default}",
        request_id=_request_id(request),
    )
    return ok(_to_out(row), message="created")


@router.post("/defaults", dependencies=_admin_gate)
def seed_default_templates(
    request: Request,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[list[TemplateOut]]:
    """Materialise the built-in default template for every entity type.

    Idempotent get-or-create, like `POST /api/codes`: an entity type that
    already has a default keeps whatever the operator edited it into, and only
    a call that actually created something writes an audit row. Returns 200
    (not 201) for the same reason — most calls create nothing.
    """
    rows, created = template_service.ensure_defaults(db, ws=ws)
    if created:
        _audit_log(
            db,
            ws=ws,
            user=user,
            action="label_template.defaults_seeded",
            target_type="label_template",
            target_ids=[row.id for row in rows if row.entity_type in created],
            comment=f"entity_types={','.join(sorted(created))}",
            request_id=_request_id(request),
        )
    return ok(
        [_to_out(row) for row in rows],
        message=f"seeded {len(created)} default template(s)",
    )


@router.get("/{template_id}")
def get_template(
    template_id: UUID, db: DbSession, ws: CurrentWorkspace
) -> Envelope[TemplateOut]:
    """Fetch one template. A foreign id is a 404, never a 403."""
    return ok(_to_out(template_service.get_template(db, ws=ws, template_id=template_id)))


@router.patch("/{template_id}")
def update_template(
    request: Request,
    template_id: UUID,
    payload: TemplateUpdate,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[TemplateOut]:
    """Partial update (admin+). Promoting to default demotes the incumbent."""
    row = _admin_template(db, ws, user, template_id)
    fields = payload.model_fields_set
    if not fields:
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            ErrorCodes.LABEL_TEMPLATE_INVALID,
            "no fields to update",
        )

    if "elements" in fields and payload.elements is not None:
        row.elements = _validate_elements(payload.elements)
    for attr in _PATCHABLE:
        if attr in fields:
            value = getattr(payload, attr)
            if value is not None:
                setattr(row, attr, value)
    if "is_default" in fields and payload.is_default is not None:
        if payload.is_default:
            template_service.clear_existing_default(
                db, ws=ws, entity_type=row.entity_type, exclude_id=row.id
            )
        row.is_default = payload.is_default

    row.updated_by = user.id
    db.flush()
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="label_template.updated",
        target_type="label_template",
        target_ids=[row.id],
        comment="fields=" + ",".join(sorted(fields)),
        request_id=_request_id(request),
    )
    return ok(_to_out(row), message="updated")


@router.delete("/{template_id}")
def delete_template(
    request: Request,
    template_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[dict]:
    """Delete a template (admin+). `POST /defaults` re-creates a built-in."""
    row = _admin_template(db, ws, user, template_id)
    entity_type = row.entity_type
    db.delete(row)
    db.flush()
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="label_template.deleted",
        target_type="label_template",
        target_ids=[template_id],
        comment=f"entity_type={entity_type}",
        request_id=_request_id(request),
    )
    return ok({"id": str(template_id)}, message="deleted")


# ---------------------------------------------------------------------------
# Operational: debug render + live test print
# ---------------------------------------------------------------------------


@router.get("/{template_id}/jscript")
def render_template_jscript(
    template_id: UUID, db: DbSession, ws: CurrentWorkspace
) -> Envelope[RenderOut]:
    """Return the JScript this template renders to, for a SAMPLE context.

    A debug view for "why is my label coming out like that?". Sample data
    only — a pure read that touches no rows and mints no object code, so it
    works on a workspace that has never printed anything.
    """
    row = template_service.get_template(db, ws=ws, template_id=template_id)
    context = template_service.sample_context(row.entity_type, ws=ws)
    return ok(RenderOut(jscript=_render_or_400(row, context)))


@router.post("/{template_id}/test-print")
@limiter.limit("20/minute", key_func=workspace_key)
def test_print(
    request: Request,
    template_id: UUID,
    payload: TestPrintIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    """Render this template and ship it to the printer.

    With no `entity_id` the label is rendered from sample data. With one, it is
    rendered for that object and its code is minted through the object-codes
    service (get-or-create) — the same code the `/c/{code}` resolver answers.

    Records a `print_jobs` row (kind `on_demand`) and walks it through the
    lifecycle. On a printer failure the job ends `failed` and this returns 409
    `printer.unreachable` with `print_job_id`, leaving the row for inspection
    (see the module docstring for why that is a return, not a raise).

    Admin+, resolved resource-first so a foreign template id is 404.
    """
    row = _admin_template(db, ws, user, template_id)

    code: str | None = None
    if payload.entity_id is not None:
        # mint_or_get does the existence + workspace check on entity_id, so a
        # foreign UUID is a 404 before anything is minted or rendered.
        context, code_row, code_created = template_service.context_for_entity(
            db, ws=ws, entity_type=row.entity_type, entity_id=payload.entity_id
        )
        code = code_row.code
        if code_created:
            _audit_log(
                db,
                ws=ws,
                user=user,
                action="object_code.minted",
                target_type="object_code",
                target_ids=[code_row.id, payload.entity_id],
                comment=f"entity_type={row.entity_type}",
                request_id=_request_id(request),
            )
    else:
        context = template_service.sample_context(row.entity_type, ws=ws)

    jscript_text = _render_or_400(row, context, copies=payload.copies)

    job, _created = print_service.get_or_create_job(
        db,
        workspace_id=ws.id,
        # A fresh key per call: a test print is an explicit "do it again"
        # action, so it must not dedupe against the previous attempt.
        idempotency_key=f"test-print:{template_id}:{uuid.uuid4().hex}",
        kind=print_service.KIND_ON_DEMAND,
        target_type="label_template",
        target_id=str(template_id),
    )

    try:
        print_service.send_jscript(db, job, jscript_text)
    except print_service.PrinterUnreachable as exc:
        _audit_log(
            db,
            ws=ws,
            user=user,
            action="label_template.test_print_failed",
            target_type="label_template",
            target_ids=[row.id, job.id],
            comment=f"entity_type={row.entity_type}",
            request_id=_request_id(request),
        )
        body = err(
            "conflict",
            "the label printer is unreachable; see the print job to reconcile",
            request_id=_request_id(request),
        )
        body["code"] = ErrorCodes.PRINTER_UNREACHABLE
        body["print_job_id"] = str(job.id)
        # The driver's reason goes to the log, not the client: it can name
        # the printer's host/port, and the caller can already read the
        # outcome off the print job.
        _log.warning(
            "label test-print failed",
            extra={"print_job_id": str(job.id), "reason": str(exc)[:500]},
        )
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=body)

    _audit_log(
        db,
        ws=ws,
        user=user,
        action="label_template.test_printed",
        target_type="label_template",
        target_ids=[row.id, job.id],
        comment=f"entity_type={row.entity_type} copies={payload.copies}",
        request_id=_request_id(request),
    )
    return ok(
        TestPrintOut(print_job_id=job.id, status=job.status, code=code),
        message="printed",
    )
