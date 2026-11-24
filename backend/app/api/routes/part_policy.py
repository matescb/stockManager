"""Workspace part policy: read / replace the rules, dry-run a create, and
report what a stored part still lacks.

Two routers because the four endpoints live under two prefixes:

* `router` at `/api/part-policy` — the rules themselves. `GET` is open to
  every member (viewers and read-only tokens included, via the member
  gate letting reads through); `PUT` is admin+.
* `parts_router` at `/api/parts` — `POST /validate` and
  `GET /{part_id}/policy-status`, next to the create they describe.

Evaluation lives in `domain/part_policy/service.py`; the create
enforcement itself is in `domain/parts/services/create_part.py` so the
REST route and the MCP tool apply it identically. Contract:
`docs/api/part-policy.md`.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.routes._parts_shared import get_part as _get_part
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession, require_role
from app.core.responses import Envelope, ok
from app.domain.audit.service import log as _audit_log
from app.domain.part_policy import service as policy
from app.domain.part_policy.schemas import PartPolicyIn
from app.domain.parts.schemas import PartIn
from app.domain.parts.services.create_part import check_references

router = APIRouter()
parts_router = APIRouter()


@router.get("")
def get_part_policy(ws: CurrentWorkspace) -> Envelope[dict]:
    """The workspace's rules plus the catalogue of fields a rule may name."""
    return ok(policy.policy_payload(ws))


@router.put("", dependencies=[Depends(require_role("admin"))])
def put_part_policy(
    request: Request,
    body: PartPolicyIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[dict]:
    """Replace the whole rule list. Admin / owner only."""
    rules = policy.replace_policy(db, ws=ws, body=body)
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="part_policy.updated",
        target_type="workspace",
        target_ids=[ws.id],
        comment=f"rules={len(rules)}",
        request_id=getattr(request.state, "request_id", None),
    )
    return ok(policy.policy_payload(ws))


@parts_router.post("/validate")
def validate_part(payload: PartIn, db: DbSession, ws: CurrentWorkspace) -> Envelope[dict]:
    """Dry-run `POST /api/parts`: `{ok, missing, existing_part}`, nothing written.

    The create's reference checks run first and answer the same way — a
    foreign category or storage location is a 404, an archived category
    a 409 — so the scope walk never sees a foreign category and `ok` never
    promises a create that would fail.
    """
    check_references(db, ws=ws, payload=payload)
    return ok(policy.validate_payload(db, ws=ws, payload=payload))


@parts_router.get("/{part_id}/policy-status")
def part_policy_status(part_id: UUID, db: DbSession, ws: CurrentWorkspace) -> Envelope[dict]:
    """`{part_id, complete, missing}` for one part against every rule."""
    part = _get_part(db, ws.id, part_id, include_archived=True)
    return ok({"part_id": str(part.id), **policy.evaluate_part(db, ws=ws, part=part)})
