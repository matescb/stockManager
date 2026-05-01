from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy import select

from app.core.deps import (
    CurrentUser,
    CurrentWorkspace,
    DbSession,
    require_role,
)
from app.core.ratelimit import limiter
from app.core.responses import ok
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace, WorkspaceInvitation, WorkspaceMember

router = APIRouter()


def _hash_token(plaintext: str) -> str:
    """SHA-256 hex digest. Used both at create time (to compute what to
    store) and at accept time (to look up by what the caller supplied).
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


class InviteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: Literal["admin", "member", "viewer"] = "member"


def _serialize(inv: WorkspaceInvitation, *, plaintext_token: str | None = None) -> dict:
    """Serialise an invitation row.

    `plaintext_token` is set ONLY in the create response — the only
    moment the plaintext exists. Subsequent reads (list, revoke) never
    have it, because the DB stores only the hash. The caller is
    responsible for delivering the plaintext to the invitee out-of-band
    immediately (typically via the link embedded in the response).
    """
    return {
        "id": str(inv.id),
        "workspace_id": str(inv.workspace_id),
        "email": inv.email,
        "role": inv.role,
        "status": inv.status,
        # Only the create response carries the plaintext; all other
        # serialisations return None here.
        "token": plaintext_token if inv.status == "pending" else None,
        "created_at": inv.created_at.isoformat(),
        "accepted_at": inv.accepted_at.isoformat() if inv.accepted_at else None,
    }


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin"))],
)
def create_invitation(
    payload: InviteIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    # Already a member?
    existing_member = (
        db.execute(
            select(WorkspaceMember, User)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(WorkspaceMember.workspace_id == ws.id)
            .where(User.email == payload.email)
        )
        .first()
    )
    if existing_member:
        raise HTTPException(status_code=409, detail="user is already a member")

    # Existing pending invite for this email — reuse rather than duplicate.
    # Note: we cannot return the existing plaintext token (we don't have
    # it). The frontend interprets a pending row with token=None as
    # "already invited; ask the operator to revoke + re-invite if the
    # invitee never received the original email".
    existing = (
        db.execute(
            select(WorkspaceInvitation)
            .where(WorkspaceInvitation.workspace_id == ws.id)
            .where(WorkspaceInvitation.email == payload.email)
            .where(WorkspaceInvitation.status == "pending")
        )
        .scalars()
        .first()
    )
    if existing:
        return ok(_serialize(existing))

    # Mint a new token. 32 bytes urlsafe ≈ 256 bits of entropy — well
    # past brute-force feasibility. The hash goes to the DB; the
    # plaintext goes back to the caller exactly once via the response.
    plaintext = secrets.token_urlsafe(32)
    inv = WorkspaceInvitation(
        workspace_id=ws.id,
        email=payload.email,
        role=payload.role,
        token_hash=_hash_token(plaintext),
        invited_by=user.id,
    )
    db.add(inv)
    db.commit()
    return ok(_serialize(inv, plaintext_token=plaintext))


@router.get("", dependencies=[Depends(require_role("admin"))])
def list_invitations(
    db: DbSession,
    ws: CurrentWorkspace,
    limit: int = Query(default=200, le=1000),
):
    rows = list(
        db.execute(
            select(WorkspaceInvitation)
            .where(WorkspaceInvitation.workspace_id == ws.id)
            .order_by(WorkspaceInvitation.created_at.desc())
            .limit(limit)
        ).scalars()
    )
    return ok([_serialize(r) for r in rows])


@router.delete("/{invitation_id}", dependencies=[Depends(require_role("admin"))])
def revoke_invitation(invitation_id: UUID, db: DbSession, ws: CurrentWorkspace):
    inv = db.get(WorkspaceInvitation, invitation_id)
    if not inv or inv.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="invitation not found")
    if inv.status != "pending":
        raise HTTPException(status_code=400, detail=f"cannot revoke a {inv.status} invitation")
    inv.status = "revoked"
    db.commit()
    return ok(None, "revoked")


# ---- Public accept endpoint (does NOT require workspace membership) ------


class AcceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str


# Token is 256-bit so brute-force is infeasible, but the endpoint is
# unauthenticated-by-workspace and could otherwise be hammered.
# 10/min/IP is well above any legitimate user's behaviour and well
# below useful enumeration speed.
@router.post("/accept")
@limiter.limit("10/minute")
def accept_invitation(request: Request, payload: AcceptIn, db: DbSession, user: CurrentUser):
    # Look up by hash — the plaintext is never stored, so an attacker
    # with a DB dump cannot mint a valid lookup query without first
    # cracking the hash (infeasible at 256 bits).
    inv = (
        db.execute(
            select(WorkspaceInvitation).where(
                WorkspaceInvitation.token_hash == _hash_token(payload.token)
            )
        )
        .scalars()
        .first()
    )
    if not inv:
        raise HTTPException(status_code=404, detail="invitation not found")
    if inv.status != "pending":
        raise HTTPException(status_code=400, detail=f"invitation is {inv.status}")
    if inv.email.lower() != user.email.lower():
        raise HTTPException(status_code=403, detail="invitation is for a different email")

    # Already a member?
    existing = (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.workspace_id == inv.workspace_id,
            WorkspaceMember.user_id == user.id,
        )
        .first()
    )
    if existing:
        existing.status = "active"
        existing.role = inv.role
    else:
        db.add(
            WorkspaceMember(
                workspace_id=inv.workspace_id,
                user_id=user.id,
                role=inv.role,
                status="active",
            )
        )
    inv.status = "accepted"
    inv.accepted_at = datetime.now(timezone.utc)
    inv.accepted_by = user.id
    db.commit()
    ws = db.get(Workspace, inv.workspace_id)
    return ok({"workspace_id": str(inv.workspace_id), "workspace_name": ws.name if ws else None, "role": inv.role})
