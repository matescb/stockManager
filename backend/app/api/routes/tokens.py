"""Personal access tokens — `/api/tokens`.

Mounted WITHOUT `_member_gate`: minting a token is not a privileged act,
because a token can never do more than its owner's membership role
already allows. A viewer's token is a viewer. Gating mint on member+
would only stop viewers from using the KiCad library (a read feature),
so the gate lives where it belongs — on the routes the token calls.

What IS gated here is token-authed access to this router itself: a
leaked token must not be able to mint a fresh one, widen itself, or
revoke the audit trail of its siblings. See `_no_token_auth`.

Thin routes; the queries and the crypto live in
`app/domain/tokens/service.py`.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from app.core.deps import (
    CurrentUser,
    CurrentWorkspace,
    DbSession,
    forbid_api_token,
    require_role,
)
from app.core.errors import ErrorCodes, raise_http
from app.core.ratelimit import limiter, user_key, workspace_key
from app.core.responses import Envelope, ok
from app.domain.audit.service import log as _audit_log
from app.domain.tokens import service as tokens_service
from app.domain.tokens.models import ApiToken
from app.domain.tokens.schemas import ApiTokenCreated, ApiTokenIn, ApiTokenOut
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace

# Every route here — GET included. A leaked token must not be able to
# widen itself (mint a longer-lived successor), clean up after itself
# (revoke the sibling whose `last_used_at` would betray the intrusion),
# or enumerate the workspace's other credentials. The same dependency
# guards the tenancy routes in `workspaces.py` / `invitations.py`.
router = APIRouter(dependencies=[Depends(forbid_api_token)])


def _serialize(row: ApiToken, email: str | None = None) -> ApiTokenOut:
    out = ApiTokenOut.model_validate(row)
    return out if email is None else out.model_copy(update={"user_email": email})


def _require_admin(db, *, user: User, ws: Workspace) -> None:
    """Admin-or-403, reusing the canonical role dependency's logic so the
    hierarchy lives in exactly one place."""
    require_role("admin")(user=user, ws=ws, db=db)


@router.get("")
def list_tokens(
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
    include_all: bool = Query(
        default=False,
        alias="all",
        description="Admin+ only: every token in the workspace, not just your own.",
    ),
) -> Envelope[list[ApiTokenOut]]:
    if not include_all:
        return ok([_serialize(row) for row in tokens_service.list_own(db, ws=ws, user=user)])

    _require_admin(db, user=user, ws=ws)
    return ok(
        [_serialize(row, email) for row, email in tokens_service.list_workspace(db, ws=ws)]
    )


@router.post("", status_code=status.HTTP_201_CREATED)
# Minting is cheap for us and expensive to clean up after, and every
# legitimate flow is a human clicking a button. 10/hour leaves room for
# a bad first attempt without leaving room for a script. Keyed per USER,
# not per workspace: a token is personal, so one member burning their
# allowance must not lock their teammates out of minting.
@limiter.limit("10/hour", key_func=user_key)
def create_token(
    request: Request,
    payload: ApiTokenIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[ApiTokenCreated]:
    """Mint a token. The response is the only place its plaintext ever
    appears — it is not recoverable afterwards, by anyone, including a
    workspace owner reading the database."""
    row, plaintext = tokens_service.mint_token(db, ws=ws, user=user, payload=payload)
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="api_token.created",
        target_type="api_token",
        target_ids=[row.id],
        # The label and the read_only flag, never the secret or its
        # digest — the audit log is readable by every workspace admin.
        comment=f"label={row.label},read_only={row.read_only}",
        request_id=getattr(request.state, "request_id", None),
    )
    created = ApiTokenCreated(**_serialize(row).model_dump(), token=plaintext)
    return ok(created)


@router.post("/{token_id}/revoke")
@limiter.limit("30/minute", key_func=workspace_key)
def revoke_token(
    request: Request,
    token_id: UUID,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
) -> Envelope[ApiTokenOut]:
    """Soft-revoke a token. Idempotent — revoking an already-revoked
    token is a 200, so a panicking user hammering the button doesn't get
    an error page."""
    row = tokens_service.get_in_workspace(db, ws=ws, token_id=token_id)
    if row is None:
        # 404 for unknown AND for another workspace's token — never 403,
        # which would confirm the id exists somewhere (ADR-0002).
        raise_http(
            status.HTTP_404_NOT_FOUND,
            ErrorCodes.RESOURCE_NOT_FOUND,
            "api token not found",
        )
    if row.user_id != user.id:
        # The row is confirmed to be in the caller's own workspace, so a
        # 403 here leaks nothing new — this is the resource-first shape
        # `_helpers.require_resource_access` uses.
        _require_admin(db, user=user, ws=ws)

    tokens_service.revoke(db, row=row, user=user)
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="api_token.revoked",
        target_type="api_token",
        target_ids=[row.id],
        comment=f"label={row.label}",
        request_id=getattr(request.state, "request_id", None),
    )
    return ok(_serialize(row))
