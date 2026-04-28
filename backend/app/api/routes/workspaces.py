from __future__ import annotations

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from app.core.deps import CurrentUser, CurrentWorkspace, DbSession
from app.core.responses import ok
from app.domain.workspaces.models import Workspace, WorkspaceMember

router = APIRouter()


class WorkspaceCreateIn(BaseModel):
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


@router.get("/current")
def current(ws: CurrentWorkspace):
    return ok(
        {
            "id": str(ws.id),
            "name": ws.name,
            "kind": ws.kind,
            "currency_default": ws.currency_default,
            "lot_control_enabled": ws.lot_control_enabled,
            "serial_tracking_enabled": ws.serial_tracking_enabled,
        }
    )


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
