from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain.users.models import User, UserSession
from app.domain.workspaces.models import Workspace, WorkspaceMember
from app.infra.db import get_db

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    request: Request,
    db: DbSession,
) -> User:
    token = request.cookies.get(settings().SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")

    sess = db.query(UserSession).filter(UserSession.token == token).first()
    if not sess:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session")
    if sess.expires_at and sess.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session expired")

    user = db.get(User, sess.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user missing")

    request.state.session_token = token
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_workspace(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    x_workspace_cookie: Annotated[str | None, Cookie(alias="stockmgr_workspace")] = None,
) -> Workspace:
    header_ws = request.headers.get("X-Workspace-Id")
    raw = header_ws or x_workspace_cookie

    membership = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.user_id == user.id, WorkspaceMember.status == "active")
        .all()
    )
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="no workspace")

    chosen: Workspace | None = None
    if raw:
        try:
            wsid = UUID(raw)
        except ValueError:
            wsid = None
        if wsid:
            for m in membership:
                if m.workspace_id == wsid:
                    chosen = db.get(Workspace, wsid)
                    break
    if chosen is None:
        chosen = db.get(Workspace, membership[0].workspace_id)
    if chosen is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace not found")
    return chosen


CurrentWorkspace = Annotated[Workspace, Depends(get_current_workspace)]


_ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}


def _membership_role(db: Session, user: User, ws: Workspace) -> str:
    m = (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.user_id == user.id,
            WorkspaceMember.workspace_id == ws.id,
            WorkspaceMember.status == "active",
        )
        .first()
    )
    return m.role if m else "viewer"


def require_role(min_role: str):
    """Dependency factory: 403 unless the current user's membership in
    the current workspace is >= min_role in the {viewer, member, admin,
    owner} hierarchy."""
    floor = _ROLE_RANK[min_role]

    def _dep(user: CurrentUser, ws: CurrentWorkspace, db: DbSession) -> None:
        rank = _ROLE_RANK.get(_membership_role(db, user, ws), 0)
        if rank < floor:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"requires role {min_role}+",
            )

    return _dep


_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def require_member_for_writes(
    request: Request,
    user: CurrentUser,
    ws: CurrentWorkspace,
    db: DbSession,
) -> None:
    """Router-level gate: any active member can read; viewer is blocked
    from writes. Use as `dependencies=[Depends(require_member_for_writes)]`
    on routers that mix read and write endpoints."""
    if request.method in _READ_METHODS:
        return
    rank = _ROLE_RANK.get(_membership_role(db, user, ws), 0)
    if rank < _ROLE_RANK["member"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="requires role member+ for write operations",
        )
