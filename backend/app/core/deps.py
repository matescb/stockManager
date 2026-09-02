from __future__ import annotations

import logging
from datetime import timedelta
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import Cookie, Depends, HTTPException, Request, status
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.core.auth import hash_session_token
from app.core.config import settings
from app.core.errors import ErrorCodes, raise_http
from app.core.time import utcnow
from app.domain.tokens import service as tokens_service
from app.domain.tokens.models import ApiToken
from app.domain.users.models import User, UserSession
from app.domain.workspaces.models import Workspace, WorkspaceMember
from app.infra.db import get_db

_log = logging.getLogger(__name__)

DbSession = Annotated[Session, Depends(get_db)]

_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def _session_idle_window() -> timedelta:
    # Sliding-expiry idle window (SEC2-015). Kept in Settings so ops can
    # tune it without changing the session model.
    return timedelta(hours=settings().SESSION_IDLE_HOURS)


def _record_token_use(db: Session, row: ApiToken, request: Request) -> bool:
    """Best-effort last-used telemetry. Returns True when a row was written
    (the throttle in `record_use` suppresses most calls). Split out as a
    module-level function so the failure path is directly testable."""
    return tokens_service.record_use(db, row, client_ip=get_remote_address(request))


def _invalid_token() -> NoReturn:
    """The single failure for every API-token rejection.

    One code, one message, one status for malformed / unknown / wrong
    secret / revoked / expired / owner-no-longer-a-member. Anything
    finer-grained is an oracle (ADR-0029).
    """
    raise_http(
        status.HTTP_401_UNAUTHORIZED,
        ErrorCodes.AUTH_INVALID_TOKEN,
        "invalid api token",
    )


def _authenticate_api_token(request: Request, db: Session, header: str) -> User:
    """Authenticate a request carrying an `Authorization` header.

    Reached for ANY non-empty Authorization header, including one whose
    scheme we don't recognise. That total-ness is load-bearing: the CSRF
    middleware skips its Origin check whenever the header is present, and
    that is only sound while a present header provably excludes cookie
    auth. If an unknown scheme fell through to the cookie path, a
    cross-site form post carrying a junk Authorization value would ride
    the victim's session with no Origin check. So: header present →
    token path → valid token or 401. Never a fallback.
    """
    scheme, _, raw = header.partition(" ")
    if scheme.lower() not in ("token", "bearer"):
        _invalid_token()

    row = tokens_service.resolve_token(db, raw.strip())
    if row is None:
        _invalid_token()

    user = db.get(User, row.user_id)
    if user is None:
        _invalid_token()

    # Membership re-check lives HERE, not in get_current_workspace, because
    # not every route depends on get_current_workspace. `GET /api/auth/me`,
    # `GET`/`POST /api/workspaces`, `/workspaces/{id}/switch` and
    # `/invitations/accept` take only CurrentUser — when this check sat in
    # the workspace dependency those five routes kept working for a token
    # whose owner had already been removed from the workspace. Authentication
    # is the one place every request passes through, so the check belongs
    # here. A lost membership is indistinguishable from a bad token (401),
    # so this can't be used to probe who is still in a workspace.
    membership = (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.user_id == row.user_id,
            WorkspaceMember.workspace_id == row.workspace_id,
            WorkspaceMember.status == "active",
        )
        .first()
    )
    if membership is None:
        _invalid_token()

    request.state.api_token = row
    # Rate-limit buckets key off this (see `core/ratelimit.py::user_key`).
    # Set on both auth paths so the bucket is the same person whichever
    # credential they used.
    request.state.user_id = str(user.id)

    # Telemetry BEFORE the read-only check, deliberately. The credential
    # is valid at this point; what follows is an authorization decision.
    # Recording only on allowed requests would make someone probing a
    # stolen read-only token with writes completely invisible in
    # `last_used_at` — exactly the pattern the field exists to surface.
    try:
        if _record_token_use(db, row, request):
            # Commit the telemetry on its own rather than letting it ride
            # the request transaction. Without this the write is lost
            # whenever the request goes on to fail — including the
            # read-only 403 below, which is exactly the probe
            # `last_used_at` exists to surface. Safe here for the same
            # reason the cookie path commits its sliding-expiry bump a few
            # lines down: authentication is the first dependency to touch
            # the DB, so there is no half-finished route work to strand.
            # The throttle keeps this to at most one commit per 300s per
            # token, so it is not a per-request cost.
            db.commit()
    except Exception:
        # Telemetry must never fail auth. Roll back so the session is
        # usable — nothing else has been written at this point, since
        # authentication is the first dependency to touch the DB. Logged
        # (not silently swallowed) so a persistently failing write is
        # visible in Sentry rather than only as a stale last_used_at.
        _log.warning(
            "api-token telemetry write failed for token %s", row.id, exc_info=True
        )
        db.rollback()

    # Read-only tokens are the credential shipped to KiCad and the PCM
    # (phases 5/6), where the plaintext ends up in a config file or a URL
    # path. Refusing writes here — before any route sees the request —
    # bounds the blast radius of that exposure.
    if row.read_only and request.method not in _READ_METHODS:
        raise_http(
            status.HTTP_403_FORBIDDEN,
            ErrorCodes.AUTH_TOKEN_READ_ONLY,
            "read-only api token",
        )

    return user


def try_authenticate_api_token(request: Request, db: Session) -> User | None:
    """API-token authentication that reports failure instead of raising.

    For surfaces that answer something other than this module's 401/403
    — `api/routes/kicad.py` answers 404 to everything, because KiCad
    treats any non-200 as "library unavailable" and a distinguishable
    401 would only be an oracle.

    Returns the authenticated user, or None when there is no
    `Authorization` header or the credential is unusable for ANY reason
    (malformed, unknown, wrong secret, revoked, expired, owner no longer
    a member, unknown scheme, or a write attempted with a read-only
    token). Callers get one failure to map onto their own status; they
    must not try to recover a reason, because there is deliberately none
    to recover.

    On success every side effect of the normal path has happened:
    `request.state.api_token` and `.user_id` are set, and the throttled
    `last_used_at` telemetry has been written and committed. That is the
    point of routing through `_authenticate_api_token` rather than
    calling the token service directly — a second implementation of the
    membership re-check and the telemetry commit would drift from this
    one, and the CSRF middleware's "a present header means token auth,
    never the cookie" rule depends on there being exactly one.
    """
    header = request.headers.get("Authorization")
    if not header:
        return None
    try:
        return _authenticate_api_token(request, db, header)
    except HTTPException:
        return None


def get_current_user(
    request: Request,
    db: DbSession,
) -> User:
    # API-token path (ADR-0029). Any non-empty Authorization header
    # commits the request to it; there is deliberately no fallback to
    # the cookie below. An empty header value is treated as absent, and
    # `CsrfOriginMiddleware` uses the identical truthiness test so the
    # two can never disagree about which path a request is on.
    auth_header = request.headers.get("Authorization")
    if auth_header:
        return _authenticate_api_token(request, db, auth_header)

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
    request.state.user_id = str(user.id)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def _workspace_for_api_token(
    request: Request, db: Session, token: ApiToken
) -> Workspace:
    """Resolve the workspace for a token-authenticated request.

    The workspace is PINNED to the one the token was minted in — a token
    is a credential for one tenant, so neither the `X-Workspace-Id`
    header nor the workspace cookie can move it.

    Membership was already verified by `_authenticate_api_token`, which
    runs for EVERY token-authed request. Do not move that check back
    here: routes that take only `CurrentUser` never reach this function.
    """
    header_ws = request.headers.get("X-Workspace-Id")
    if header_ws:
        # A client that sends a workspace header AND a token is either
        # confused or trying to pivot. Echoing the pinned workspace back
        # would silently do the wrong thing, so surface it distinctly —
        # unlike the token-validity failures, this one leaks nothing an
        # attacker doesn't already hold (they have the token, and its
        # workspace is implied by every response it gets).
        try:
            requested = UUID(header_ws)
        except ValueError:
            requested = None
        if requested != token.workspace_id:
            raise_http(
                status.HTTP_403_FORBIDDEN,
                ErrorCodes.AUTH_TOKEN_WORKSPACE_MISMATCH,
                "token is not valid for the requested workspace",
            )

    ws = db.get(Workspace, token.workspace_id)
    if ws is None:
        _invalid_token()

    request.state.workspace_id = str(ws.id)
    return ws


def get_current_workspace(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    x_workspace_cookie: Annotated[str | None, Cookie(alias="stockmgr_workspace")] = None,
) -> Workspace:
    api_token = getattr(request.state, "api_token", None)
    if api_token is not None:
        return _workspace_for_api_token(request, db, api_token)

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


def api_token_workspace_id(request: Request) -> UUID | None:
    """The workspace an API-token request is pinned to, or None for a
    cookie session.

    Routes that list things *across* workspaces (`/auth/me`,
    `GET /api/workspaces`) must narrow their results to this id — a
    token is a credential for one tenant and must not enumerate the
    others its owner happens to belong to.
    """
    token = getattr(request.state, "api_token", None)
    return token.workspace_id if token is not None else None


def forbid_api_token(request: Request, user: CurrentUser) -> None:
    """Dependency: refuse any request that authenticated with an API token.

    For routes that administer credentials or tenancy — minting and
    revoking tokens, creating a workspace, switching the active one,
    accepting or issuing an invitation. A leaked token must not be able
    to widen itself (mint a longer-lived successor, or invite an
    accomplice), clean up after itself (revoke the sibling whose
    `last_used_at` would betray the intrusion), or move its owner
    between tenants. Those are human-at-a-browser actions, so they
    require the session cookie.

    Takes `user` purely to order the dependency graph: router- and
    route-level dependencies resolve before the endpoint's own, so
    without this `request.state.api_token` would not be set yet and the
    gate would wave every token through.
    """
    if getattr(request.state, "api_token", None) is not None:
        raise_http(
            status.HTTP_403_FORBIDDEN,
            ErrorCodes.AUTH_TOKEN_NO_TOKEN_MANAGEMENT,
            "api tokens cannot manage credentials or workspace membership",
        )


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
