"""Universal object codes — `/api/codes`.

Two endpoints, both workspace-scoped:

* `POST /api/codes` — get-or-create the short code for one object.
  Minting is lazy: nothing has a code until someone asks for one (which
  in practice means "until someone prints a label for it").
* `GET /api/codes/{code}` — the scan path. Resolves a code back to
  `{entity_type, entity_id}` so the client can navigate to the object.

Thin routes: the generator, the normaliser and every query live in
`app/domain/codes/service.py`. Writes are member-gated by `_member_gate`
in `main.py` and rate-limited per workspace.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import Envelope, ok
from app.domain.audit.service import log as _audit_log
from app.domain.codes import service as codes_service
from app.domain.codes.schemas import ObjectCodeIn, ObjectCodeOut

router = APIRouter()


@router.post("")
@limiter.limit("60/minute", key_func=workspace_key)
def mint_code(
    request: Request,
    payload: ObjectCodeIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[ObjectCodeOut]:
    """Return this object's code, minting one on first call.

    Idempotent — calling twice yields the same code, and only the call
    that actually minted writes an audit row. Returns 200 rather than 201
    for the same reason: most calls create nothing.
    """
    row, created = codes_service.mint_or_get(
        db,
        ws=ws,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
    )
    if created:
        # A code is printed on a label, not a credential — but the audit
        # comment stays a low-sensitivity summary per the CLAUDE.md
        # audit invariant, so it records *what kind of thing* got a code,
        # not the code itself. The row id is in `target_ids`.
        _audit_log(
            db,
            ws=ws,
            user=user,
            action="object_code.minted",
            target_type="object_code",
            target_ids=[row.id, payload.entity_id],
            comment=f"entity_type={payload.entity_type}",
            request_id=getattr(request.state, "request_id", None),
        )
    return ok(ObjectCodeOut.model_validate(row))


@router.get("/{code}")
@limiter.limit("120/minute", key_func=workspace_key)
def resolve_code(
    request: Request,
    code: str,
    db: DbSession,
    ws: CurrentWorkspace,
) -> Envelope[ObjectCodeOut]:
    """Resolve a scanned code to the object it names.

    404 for unknown, malformed, and other-workspace codes alike — see
    `ErrorCodes.CODE_NOT_FOUND`.
    """
    row = codes_service.resolve(db, ws=ws, code=code)
    return ok(ObjectCodeOut.model_validate(row))
