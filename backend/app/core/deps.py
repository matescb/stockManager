from __future__ import annotations

from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Cookie, Depends, Request, status
from sqlalchemy.orm import Session

from app.core.auth import hash_session_token
from app.core.config import settings
from app.core.errors import ErrorCodes, raise_http
from app.core.time import utcnow
from app.domain.users.models import User, UserSession
from app.domain.workspaces.models import Workspace, WorkspaceMember
from app.infra.db import get_db

DbSession = Annotated[Session, Depends(get_db)]


def _session_idle_window() -> timedelta:
    # Sliding-expiry idle window (SEC2-015). Kept in Settings so ops can
    # tune it without changing the session model.
    return timedelta(hours=settings().SESSION_IDLE_HOURS)


def get_current_user(
    request: Request,
    db: DbSession,
) -> User:
    token = request.cookies.get(settings().SESSION_COOKIE_NAME)
    if not token:
        raise_http(
            status.HTTP_401_UNAUTHORIZED,
            ErrorCodes.AUTH_NOT_AUTHENTICATED,
            "not authenticated",
        )

    # The DB only ever holds the SHA-256 digest of the token (SEC2-003).
    # Equality on a pre-image-resistant hash is fine here; we don't need
    # hmac.compare_digest because `token_hash` IS the primary key —
    # the lookup is a PK equality check, not a timing-oracle scan.
    # (SEC2-013 documents why invitation tokens needed a different fix:
    # they were looked up by a non-PK hash column, so the SQL comparison
    # itself was the timing oracle.  Session tokens don't have that
    # problem — the hash is the PK, so Postgres uses a hash-index equality
    # check that reveals nothing about prefix matches.)
    digest = hash_session_token(token)
    sess = db.query(UserSession).filter(UserSession.token_hash == digest).first()
    if not sess:
        raise_http(
            status.HTTP_401_UNAUTHORIZED,
            ErrorCodes.AUTH_INVALID_SESSION,
            "invalid session",
        )
    now = utcnow()
    if sess.expires_at and sess.expires_at < now:
        raise_http(
            status.HTTP_401_UNAUTHORIZED,
            ErrorCodes.AUTH_SESSION_EXPIRED,
            "session expired",
        )
    if sess.last_used_at and sess.last_used_at < now - _session_idle_window():
        # SEC2-015: idle longer than the sliding window. Drop the row
        # so a re-login mints a fresh credential rather than reviving
        # this one.
        db.delete(sess)
        db.commit()
        raise_http(
            status.HTTP_401_UNAUTHORIZED,
            ErrorCodes.AUTH_SESSION_IDLE_TIMEOUT,
            "session idle timeout",
        )

    user = db.get(User, sess.user_id)
    if not user:
        raise_http(
            status.HTTP_401_UNAUTHORIZED,
            ErrorCodes.AUTH_USER_MISSING,
            "user missing",
        )

    # Sliding expiry: bump last_used_at on every successful auth. Commit
    # is cheap (single row update by PK); the alternative — relying on
    # the route's own commit — leaves dangling sessions on read-only
    # GETs that never touch the session.
    sess.last_used_at = now
    db.commit()

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
        raise_http(
            status.HTTP_403_FORBIDDEN,
            ErrorCodes.WORKSPACE_NONE,
            "no workspace",
        )

    chosen: Workspace | None = None
    if raw:
        # The /workspaces/{id}/switch route now parses workspace_id as
        # UUID upstream (SEC2-004), so the cookie can no longer carry
        # garbage. The X-Workspace-Id header, however, is untrusted
        # client input — a malformed value here must produce a clean
        # 4xx, not a 500. Keep the try/except as defence-in-depth.
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
        # Reached only when the membership row references a workspace
        # row that no longer exists (orphaned membership). Distinct
        # from cross-workspace probing — those return 404 from the
        # /switch route. Status code preserved at 403 to avoid changing
        # public behaviour in this PR; see issue #125.
        raise_http(
            status.HTTP_403_FORBIDDEN,
            ErrorCodes.WORKSPACE_NOT_FOUND,
            "workspace not found",
        )
    # SEC2-017: expose workspace_id on request state so rate-limit key
    # functions can bucket by workspace rather than IP alone. This is
    # safe to set here because we've already verified membership above.
    request.state.workspace_id = str(chosen.id)
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
            raise_http(
                status.HTTP_403_FORBIDDEN,
                ErrorCodes.RESOURCE_INSUFFICIENT_ROLE,
                f"requires role {min_role}+",
                required_role=min_role,
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
        raise_http(
            status.HTTP_403_FORBIDDEN,
            ErrorCodes.RESOURCE_INSUFFICIENT_ROLE,
            "requires role member+ for write operations",
            required_role="member",
        )
