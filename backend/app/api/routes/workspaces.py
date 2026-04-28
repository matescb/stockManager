from __future__ import annotations

import secrets
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.deps import CurrentUser, CurrentWorkspace, DbSession, require_role
from app.core.responses import ok
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace, WorkspaceMember

router = APIRouter()


class WorkspaceCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    currency_default: str = "USD"


@router.get("")
def list_workspaces(user: CurrentUser, db: DbSession):
    memberships = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.user_id == user.id, WorkspaceMember.status == "active")
        .all()
    )
    out = []
    for m in memberships:
        ws = db.get(Workspace, m.workspace_id)
        if ws:
            out.append({"id": str(ws.id), "name": ws.name, "kind": ws.kind, "currency_default": ws.currency_default})
    return ok(out)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_workspace(payload: WorkspaceCreateIn, user: CurrentUser, db: DbSession):
    ws = Workspace(name=payload.name, kind="organization", owner_user_id=user.id, currency_default=payload.currency_default)
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner", status="active"))
    db.commit()
    return ok({"id": str(ws.id), "name": ws.name})


def _catalog_url(ws: Workspace) -> str | None:
    if ws.catalog_enabled and ws.catalog_token:
        return f"/catalog/{ws.catalog_token}"
    return None


def _serialize_workspace(ws: Workspace) -> dict:
    return {
        "id": str(ws.id),
        "name": ws.name,
        "kind": ws.kind,
        "currency_default": ws.currency_default,
        "lot_control_enabled": ws.lot_control_enabled,
        "serial_tracking_enabled": ws.serial_tracking_enabled,
        "catalog_enabled": bool(ws.catalog_enabled),
        "catalog_url": _catalog_url(ws),
    }


@router.get("/current")
def current(ws: CurrentWorkspace):
    return ok(_serialize_workspace(ws))


class WorkspacePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    currency_default: str | None = Field(default=None, min_length=3, max_length=3)
    lot_control_enabled: bool | None = None
    serial_tracking_enabled: bool | None = None
    catalog_enabled: bool | None = None
    # Write-only command flag: when true (and the catalog stays enabled), the
    # route mints a fresh secrets.token_urlsafe(32) and stores it.
    regenerate_catalog_token: bool | None = None


@router.patch("/current", dependencies=[Depends(require_role("admin"))])
def patch_current(payload: WorkspacePatch, db: DbSession, ws: CurrentWorkspace):
    data = payload.model_dump(exclude_unset=True)
    regenerate = bool(data.pop("regenerate_catalog_token", False))
    was_enabled = bool(ws.catalog_enabled)
    for k, v in data.items():
        setattr(ws, k, v)
    # Mint a token when enabling the catalog for the first time, or when the
    # caller explicitly asks for a fresh one while it's enabled.
    if ws.catalog_enabled and (regenerate or not ws.catalog_token or not was_enabled):
        ws.catalog_token = secrets.token_urlsafe(32)
    db.commit()
    return ok(_serialize_workspace(ws))


@router.get("/members")
def list_members(db: DbSession, ws: CurrentWorkspace):
    rows = list(
        db.execute(
            select(WorkspaceMember, User)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(WorkspaceMember.workspace_id == ws.id)
            .order_by(User.name)
        )
    )
    return ok(
        [
            {
                "id": str(m.id),
                "user_id": str(u.id),
                "email": u.email,
                "name": u.name,
                "role": m.role,
                "status": m.status,
            }
            for m, u in rows
        ]
    )


class MemberPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["owner", "admin", "member", "viewer"] | None = None
    status: Literal["active", "disabled"] | None = None


def _active_owner_count(db, ws_id):
    return len(
        db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == ws_id,
                WorkspaceMember.role == "owner",
                WorkspaceMember.status == "active",
            )
        ).scalars().all()
    )


@router.patch("/members/{member_id}", dependencies=[Depends(require_role("admin"))])
def patch_member(member_id: UUID, payload: MemberPatch, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    m = db.get(WorkspaceMember, member_id)
    if not m or m.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="member not found")
    target_promotion_to_owner = payload.role == "owner"
    target_was_owner = m.role == "owner"
    if target_promotion_to_owner or target_was_owner:
        my_role = (
            db.query(WorkspaceMember)
            .filter(WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == user.id)
            .first()
        )
        if not my_role or my_role.role != "owner":
            raise HTTPException(status_code=403, detail="only owners can manage owner role")
    if target_was_owner and (payload.role and payload.role != "owner"):
        if _active_owner_count(db, ws.id) <= 1:
            raise HTTPException(status_code=400, detail="cannot demote the last owner")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(m, k, v)
    db.commit()
    return ok({"id": str(m.id), "role": m.role, "status": m.status})


@router.delete("/members/{member_id}", dependencies=[Depends(require_role("admin"))])
def remove_member(member_id: UUID, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    m = db.get(WorkspaceMember, member_id)
    if not m or m.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="member not found")
    if m.user_id == user.id:
        raise HTTPException(status_code=400, detail="cannot remove yourself; transfer ownership first")
    if m.role == "owner" and _active_owner_count(db, ws.id) <= 1:
        raise HTTPException(status_code=400, detail="cannot remove the last owner")
    db.delete(m)
    db.commit()
    return ok(None, "removed")


@router.post("/{workspace_id}/switch")
def switch_workspace(workspace_id: str, response: Response):
    response.set_cookie(
        key="stockmgr_workspace",
        value=workspace_id,
        httponly=False,
        secure=False,
        samesite="lax",
        max_age=365 * 24 * 3600,
        path="/",
    )
    return ok({"workspace_id": workspace_id})
