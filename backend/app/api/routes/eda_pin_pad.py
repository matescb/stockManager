"""The pin/pad check on the CAD tab, and accepting its findings (SM-008b).

Mounted under `/api/parts` beside `eda.parts_router`:

* `GET    /{part_id}/eda/pin-pad` — the `check_pin_pad` payload (the
  same object MCP returns, acceptances applied) plus `can_accept`:
  whether THIS caller may accept findings, so the tab shows the button.
* `POST   /{part_id}/eda/pin-pad/accept` — `{finding_kind, number, note}`;
  accept one finding on the part's current files.
* `DELETE /{part_id}/eda/pin-pad/accept?finding_kind=&number=` — revoke
  it (every acceptance of that finding on the part, stale ones too).

Accepting is a person's judgement that a finding is fine, so it takes a
workspace `owner` or `admin` signed in with a browser SESSION. Every
API token — however privileged its owner — and every lesser role is
refused with `eda.review_requires_owner`, the code and rule SM-010's
file review uses (PR #962). MCP reads acceptances and never writes them.

The rules (what can be accepted, notes, staleness) live in
`domain/eda/pin_pad_acceptance.py`.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes._eda_shared import audit as _audit
from app.api.routes._parts_shared import get_part as _get_part
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession, membership_role
from app.core.errors import ErrorCodes, raise_http
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import Envelope, ok
from app.domain.eda import pin_pad_acceptance
from app.domain.eda.pin_pad_service import check_parts

parts_router = APIRouter()

_ACCEPT_RATE = "60/minute"
_ACCEPT_ROLES = frozenset({"owner", "admin"})

FindingKind = Literal["pad_without_pin", "pin_without_pad", "duplicate_pin"]


class PinPadAcceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_kind: FindingKind
    number: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=pin_pad_acceptance.NOTE_MAX)


def _may_accept(request: Request, db, user, ws) -> bool:
    if getattr(request.state, "api_token", None) is not None:
        return False
    return membership_role(db, user, ws) in _ACCEPT_ROLES


def require_owner_session(
    request: Request, user: CurrentUser, ws: CurrentWorkspace, db: DbSession
) -> None:
    """Dependency: an owner/admin browser session, never an API token.

    Takes `user` so token authentication has run (and set
    `request.state.api_token`) before this reads it — the ordering
    reason `deps.forbid_api_token` gives.
    """
    if not _may_accept(request, db, user, ws):
        raise_http(
            status.HTTP_403_FORBIDDEN,
            ErrorCodes.EDA_REVIEW_REQUIRES_OWNER,
            "a pin/pad finding is accepted by a workspace owner or admin signed "
            "in to the app; API tokens and other roles cannot",
        )


def _check(db, ws, part) -> dict:
    return check_parts(db, ws.id, [part])[part.id]


@parts_router.get("/{part_id}/eda/pin-pad")
def get_part_pin_pad(
    request: Request,
    part_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[dict]:
    part = _get_part(db, ws.id, part_id, include_archived=True)
    return ok({**_check(db, ws, part), "can_accept": _may_accept(request, db, user, ws)})


@parts_router.post(
    "/{part_id}/eda/pin-pad/accept",
    dependencies=[Depends(require_owner_session)],
)
@limiter.limit(_ACCEPT_RATE, key_func=workspace_key)
def accept_part_pin_pad_finding(
    request: Request,
    part_id: UUID,
    payload: PinPadAcceptIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[dict]:
    part = _get_part(db, ws.id, part_id)
    done = pin_pad_acceptance.accept(
        db,
        ws_id=ws.id,
        part=part,
        user_id=user.id,
        kind=payload.finding_kind,
        number=payload.number,
        note=payload.note,
    )
    # Kind, class and the file hashes only — the pad number is the
    # footprint's own label and the note is free text.
    _audit(
        request,
        db,
        ws,
        user,
        action="eda_pin_pad.accepted",
        target_type="part_eda",
        target_id=part.id,
        comment=(
            f"kind={payload.finding_kind},class={done.row.finding_class},"
            f"symbol_sha256={done.row.symbol_sha256},"
            f"footprint_sha256={done.row.footprint_sha256}"
        ),
    )
    return ok(_check(db, ws, part))


@parts_router.delete(
    "/{part_id}/eda/pin-pad/accept",
    dependencies=[Depends(require_owner_session)],
)
@limiter.limit(_ACCEPT_RATE, key_func=workspace_key)
def revoke_part_pin_pad_finding(
    request: Request,
    part_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
    finding_kind: FindingKind = Query(...),
    number: str = Query(..., min_length=1, max_length=100),
) -> Envelope[dict]:
    part = _get_part(db, ws.id, part_id)
    removed = pin_pad_acceptance.revoke(
        db, ws_id=ws.id, part=part, kind=finding_kind, number=number
    )
    _audit(
        request,
        db,
        ws,
        user,
        action="eda_pin_pad.revoked",
        target_type="part_eda",
        target_id=part.id,
        comment=f"kind={finding_kind},removed={removed}",
    )
    return ok(_check(db, ws, part))
