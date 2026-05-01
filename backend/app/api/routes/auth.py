from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.auth import (
    WeakPasswordError,
    create_session_row,
    hash_password,
    revoke_session,
    validate_password_strength,
    verify_password,
)
from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.core.ratelimit import limiter
from app.core.responses import ok
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace, WorkspaceMember

router = APIRouter()
log = get_logger(__name__)


class SignupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=200)
    workspace_name: str | None = None


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings().SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        # Mark Secure in prod where the proxy terminates TLS, so browsers
        # never re-send the cookie over plaintext. Stays off in dev where
        # we serve plain HTTP on localhost.
        secure=settings().APP_ENV == "prod",
        samesite="lax",
        max_age=settings().SESSION_LIFETIME_DAYS * 24 * 3600,
        path="/",
    )


# Sign-up rate limit: legitimate humans rarely create more than one account
# per IP per hour. Tighter than login because failed signups are a stronger
# spam / reputation signal than a forgotten password.
@router.post("/signup")
@limiter.limit("5/hour")
def signup(request: Request, payload: SignupIn, response: Response, db: DbSession):
    try:
        validate_password_strength(payload.password)
    except WeakPasswordError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        log.warning("signup conflict", extra={"email": payload.email})
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
    log.info("signup", extra={"user_id": str(user.id), "workspace_id": str(ws.id)})
    return ok({"user": {"id": str(user.id), "email": user.email, "name": user.name}, "workspace_id": str(ws.id)})


# Login rate limit: 10 / minute per IP is enough for a human fat-fingering
# their password, far short of viable for online password stuffing.
@router.post("/login")
@limiter.limit("10/minute")
def login(request: Request, payload: LoginIn, response: Response, db: DbSession):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(user.password_hash, payload.password):
        # Log failures (without the password). Useful for spotting
        # brute-force patterns alongside the slowapi rate-limit.
        log.warning("login failed", extra={"email": payload.email})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    sess = create_session_row(db, user.id)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    _set_session_cookie(response, sess.token)
    log.info("login", extra={"user_id": str(user.id)})
    return ok({"user": {"id": str(user.id), "email": user.email, "name": user.name}})


@router.post("/logout")
def logout(request: Request, response: Response, db: DbSession):
    cookie_name = settings().SESSION_COOKIE_NAME
    token = request.cookies.get(cookie_name)
    if token:
        revoke_session(db, token)
        db.commit()
    response.delete_cookie(cookie_name, path="/")
    log.info("logout")
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
