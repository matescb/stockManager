from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select

from app.core.deps import (
    CurrentUser,
    CurrentWorkspace,
    DbSession,
    require_role,
)
from app.core.responses import ok
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace, WorkspaceInvitation, WorkspaceMember

router = APIRouter()


class InviteIn(BaseModel):
    email: EmailStr
    role: Literal["admin", "member", "viewer"] = "member"


def _serialize(inv: WorkspaceInvitation) -> dict:
    return {
        "id": str(inv.id),
        "workspace_id": str(inv.workspace_id),
        "email": inv.email,
        "role": inv.role,
        "status": inv.status,
        "token": inv.token if inv.status == "pending" else None,
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

    inv = WorkspaceInvitation(
        workspace_id=ws.id,
        email=payload.email,
        role=payload.role,
        token=secrets.token_urlsafe(32),
        invited_by=user.id,
    )
    db.add(inv)
    db.commit()
    return ok(_serialize(inv))


@router.get("", dependencies=[Depends(require_role("admin"))])
def list_invitations(db: DbSession, ws: CurrentWorkspace):
    rows = list(
        db.execute(
            select(WorkspaceInvitation)
            .where(WorkspaceInvitation.workspace_id == ws.id)
            .order_by(WorkspaceInvitation.created_at.desc())
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
    token: str


@router.post("/accept")
def accept_invitation(payload: AcceptIn, db: DbSession, user: CurrentUser):
    inv = (
        db.execute(
            select(WorkspaceInvitation).where(WorkspaceInvitation.token == payload.token)
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
