from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from app.core.auth import (
    create_session_row,
    hash_password,
    revoke_session,
    verify_password,
)
from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.core.responses import ok
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace, WorkspaceMember

router = APIRouter()


class SignupIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=200)
    workspace_name: str | None = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings().SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=False,  # set True behind HTTPS in prod
        samesite="lax",
        max_age=settings().SESSION_LIFETIME_DAYS * 24 * 3600,
        path="/",
    )


@router.post("/signup")
def signup(payload: SignupIn, response: Response, db: DbSession):
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email already registered")
    user = User(email=payload.email, name=payload.name, password_hash=hash_password(payload.password))
    db.add(user)
    db.flush()

    # Personal workspace + membership
    ws = Workspace(name=payload.workspace_name or f"{payload.name}'s workspace", kind="personal", owner_user_id=user.id)
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner", status="active"))

    sess = create_session_row(db, user.id)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    _set_session_cookie(response, sess.token)
    return ok({"user": {"id": str(user.id), "email": user.email, "name": user.name}, "workspace_id": str(ws.id)})


@router.post("/login")
def login(payload: LoginIn, response: Response, db: DbSession):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(user.password_hash, payload.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    sess = create_session_row(db, user.id)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    _set_session_cookie(response, sess.token)
    return ok({"user": {"id": str(user.id), "email": user.email, "name": user.name}})


@router.post("/logout")
def logout(request: Request, response: Response, db: DbSession):
    cookie_name = settings().SESSION_COOKIE_NAME
    token = request.cookies.get(cookie_name)
    if token:
        revoke_session(db, token)
        db.commit()
    response.delete_cookie(cookie_name, path="/")
    return ok(None, "logged out")


@router.get("/me")
def me(user: CurrentUser, db: DbSession):
    memberships = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.user_id == user.id, WorkspaceMember.status == "active")
        .all()
    )
    workspaces = []
    for m in memberships:
        ws = db.get(Workspace, m.workspace_id)
        if ws:
            workspaces.append({"id": str(ws.id), "name": ws.name, "kind": ws.kind})
    return ok(
        {
            "user": {"id": str(user.id), "email": user.email, "name": user.name},
            "workspaces": workspaces,
        }
    )
