from __future__ import annotations

import hmac as _hmac
import secrets
from datetime import timedelta
from uuid import UUID

from app.core.time import utcnow

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.auth import (
    WeakPasswordError,
    check_login_lockout,
    clear_login_failures,
    create_session_row,
    hash_password,
    record_login_failure,
    revoke_all_user_sessions,
    revoke_session,
    validate_password_strength,
    verify_password,
)
from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.core.errors import ErrorCodes, raise_http
from app.core.logging import get_logger
from app.core.mail import send_verification_email
from app.core.ratelimit import limiter
from app.core.responses import Envelope, ok
from app.domain.users.models import PendingUser, User
from app.domain.workspaces.models import Workspace, WorkspaceMember

router = APIRouter()
log = get_logger(__name__)

# How long a pending signup verification is valid (in hours).
_VERIFY_TTL_HOURS = 24


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


def _hmac_token(plaintext: str) -> str:
    """HMAC-SHA-256 (keyed on SESSION_SECRET) hex digest of the plaintext.

    Used for the email-verification token stored in `pending_users.
    verification_token_hmac`.  The accept flow compares via
    `hmac.compare_digest(_hmac_token(supplied), row.verification_token_hmac)`
    for constant-time comparison (SEC2-013 pattern).
    """
    key = settings().SESSION_SECRET.encode("utf-8")
    return _hmac.new(key, plaintext.encode("utf-8"), "sha256").hexdigest()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


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


class VerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    token: str


# ---------------------------------------------------------------------------
# Sign-up — creates a PendingUser and sends verification email
# ---------------------------------------------------------------------------

# Sign-up rate limit: legitimate humans rarely create more than one account
# per IP per hour. Tighter than login because failed signups are a stronger
# spam / reputation signal than a forgotten password.
@router.post("/signup")
@limiter.limit("5/hour")
def signup(
    request: Request, payload: SignupIn, response: Response, db: DbSession
) -> Envelope[dict]:
    """Handle account signup.

    Behaviour depends on SIGNUP_REQUIRE_EMAIL_VERIFICATION (SEC2-014):

    * True (prod default): create a PendingUser row, send verification email,
      return 202 Accepted. No User/Workspace is created yet.
    * False (dev/test default): create User + Workspace immediately and issue
      a session cookie — same as the original behaviour. Returns 200 OK.

    The flag is forced to True when APP_ENV == "prod" by the Settings
    model_validator, so the email-verification path is always active in
    production even if the env var is omitted.
    """
    try:
        validate_password_strength(payload.password)
    except WeakPasswordError as exc:
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            ErrorCodes.AUTH_WEAK_PASSWORD,
            str(exc),
        )

    # Reject if there is already a verified User with this email.
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        log.warning("signup conflict", extra={"email": payload.email})
        raise_http(
            status.HTTP_409_CONFLICT,
            ErrorCodes.AUTH_EMAIL_TAKEN,
            "email already registered",
        )

    if not settings().SIGNUP_REQUIRE_EMAIL_VERIFICATION:
        # --- Dev / legacy path: immediate signup (no email verification) ---
        user = User(
            email=payload.email,
            name=payload.name,
            password_hash=hash_password(payload.password),
        )
        db.add(user)
        db.flush()

        ws = Workspace(
            name=payload.workspace_name or f"{payload.name}'s workspace",
            kind="personal",
            owner_user_id=user.id,
        )
        db.add(ws)
        db.flush()
        db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner", status="active"))

        sess = create_session_row(db, user.id)
        user.last_login_at = utcnow()

        _set_session_cookie(response, sess.token)
        log.info("signup (immediate)", extra={"user_id": str(user.id), "workspace_id": str(ws.id)})
        return ok(
            {"user": {"id": str(user.id), "email": user.email, "name": user.name}, "workspace_id": str(ws.id)},
        )

    # --- Prod path: email-verification two-step flow ---

    # Reap expired pending rows for this email before checking for an
    # active pending one, so old unverified attempts don't block a retry.
    cutoff = utcnow() - timedelta(hours=_VERIFY_TTL_HOURS)
    db.query(PendingUser).filter(
        PendingUser.email == payload.email,
        PendingUser.created_at < cutoff,
        PendingUser.verified_at.is_(None),
    ).delete(synchronize_session=False)

    # If there's already a non-expired pending row, return 202 again
    # without creating a duplicate row. The user may click the first
    # link or wait for it to expire and re-sign-up.
    existing_pending = (
        db.query(PendingUser)
        .filter(
            PendingUser.email == payload.email,
            PendingUser.verified_at.is_(None),
        )
        .first()
    )
    if existing_pending:
        log.info("signup resent existing pending", extra={"email": payload.email})
        response.status_code = status.HTTP_202_ACCEPTED
        return ok(
            {"status": "verification_sent"},
            "verification email sent",
        )

    # Mint a verification token, store its HMAC, send the link.
    plaintext_token = secrets.token_urlsafe(32)
    pending = PendingUser(
        email=payload.email,
        name=payload.name,
        password_hash=hash_password(payload.password),
        workspace_name=payload.workspace_name,
        verification_token_hmac=_hmac_token(plaintext_token),
        ip=request.client.host if request.client else None,
    )
    db.add(pending)
    db.flush()  # populate pending.id

    # Build verification link and send the email.
    link = f"{settings().APP_BASE_URL}/verify?id={pending.id}&token={plaintext_token}"
    try:
        send_verification_email(to=payload.email, verification_link=link)
    except Exception as exc:
        log.error("failed to send verification email to %s: %s", payload.email, exc)
        raise_http(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "mail.send_failed",
            "could not send verification email — please try again later",
        )

    log.info("signup pending", extra={"pending_id": str(pending.id)})
    response.status_code = status.HTTP_202_ACCEPTED
    return ok(
        {"status": "verification_sent"},
        "verification email sent",
    )


# ---------------------------------------------------------------------------
# Verify — consumes PendingUser, creates User + Workspace + session
# ---------------------------------------------------------------------------

# Verification rate limit: protect against token brute-force.
@router.post("/verify")
@limiter.limit("10/minute")
def verify(
    request: Request, payload: VerifyIn, response: Response, db: DbSession
) -> Envelope[dict]:
    # Look up by id (PK) — no timing oracle on the SQL lookup.
    try:
        pending_id = UUID(payload.id)
    except ValueError:
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            ErrorCodes.AUTH_VERIFICATION_INVALID,
            "invalid verification link",
        )

    pending = db.get(PendingUser, pending_id)

    # Constant-time HMAC comparison regardless of whether the row exists.
    supplied_hmac = _hmac_token(payload.token)
    dummy_hmac = "0" * 64  # same length as a SHA-256 hex digest
    stored_hmac = pending.verification_token_hmac if pending is not None else dummy_hmac

    token_ok = _hmac.compare_digest(supplied_hmac, stored_hmac) and pending is not None

    if not token_ok:
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            ErrorCodes.AUTH_VERIFICATION_INVALID,
            "invalid or expired verification link",
        )

    # At this point pending is not None (token_ok implies it).
    assert pending is not None  # noqa: S101 — mypy / type narrowing

    if pending.verified_at is not None:
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            ErrorCodes.AUTH_VERIFICATION_INVALID,
            "verification link already used",
        )

    cutoff = utcnow() - timedelta(hours=_VERIFY_TTL_HOURS)
    if pending.created_at < cutoff:
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            ErrorCodes.AUTH_VERIFICATION_EXPIRED,
            "verification link expired — please sign up again",
        )

    # Check the email isn't already taken (race-condition guard).
    if db.query(User).filter(User.email == pending.email).first():
        raise_http(
            status.HTTP_409_CONFLICT,
            ErrorCodes.AUTH_EMAIL_TAKEN,
            "email already registered",
        )

    # Promote: create User + Workspace + WorkspaceMember in one transaction.
    pending.verified_at = utcnow()

    user = User(
        email=pending.email,
        name=pending.name,
        password_hash=pending.password_hash,
    )
    db.add(user)
    db.flush()

    ws = Workspace(
        name=pending.workspace_name or f"{pending.name}'s workspace",
        kind="personal",
        owner_user_id=user.id,
    )
    db.add(ws)
    db.flush()
    db.add(
        WorkspaceMember(
            workspace_id=ws.id,
            user_id=user.id,
            role="owner",
            status="active",
        )
    )

    sess = create_session_row(db, user.id)
    user.last_login_at = utcnow()

    _set_session_cookie(response, sess.token)
    log.info(
        "signup verified",
        extra={"user_id": str(user.id), "workspace_id": str(ws.id)},
    )
    return ok(
        {"user": {"id": str(user.id), "email": user.email, "name": user.name}, "workspace_id": str(ws.id)},
        "email verified",
    )


# ---------------------------------------------------------------------------
# Login — with per-account lockout
# ---------------------------------------------------------------------------

# Login rate limit: 10 / minute per IP is enough for a human fat-fingering
# their password, far short of viable for online password stuffing.  The
# per-account lockout below provides an additional layer.
@router.post("/login")
@limiter.limit("10/minute")
def login(
    request: Request, payload: LoginIn, response: Response, db: DbSession
) -> Envelope[dict]:
    client_ip = request.client.host if request.client else None

    # Per-account lockout check (SEC2-014).
    # We check BEFORE the credential comparison so a locked account
    # always gets the lockout response, not a generic "invalid credentials".
    # The check is constant-time on the DB side regardless of email existence.
    if check_login_lockout(db, email=payload.email):
        raise_http(
            status.HTTP_429_TOO_MANY_REQUESTS,
            ErrorCodes.AUTH_ACCOUNT_LOCKED,
            "too many failed login attempts — try again later",
            retry_after_seconds=LOCKOUT_WINDOW_SECONDS,
        )

    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(user.password_hash, payload.password):
        # Record the failure before raising so the row is committed even
        # though the route exits via HTTPException.  The DB session is
        # committed by the dep on clean exit; on exception we must flush
        # manually and let the DB dep roll back (but we've already added
        # the row — the dep rolls the whole tx including this row, so we
        # commit explicitly here via a nested flush → the session
        # auto-commits when the dep closes it without an exception).
        #
        # Implementation: record_login_failure adds the row; the dep
        # commits on 200 but rolls back on exception.  To ensure the
        # failure row is persisted even when we're about to raise, we
        # flush and commit here before raising.
        record_login_failure(db, email=payload.email, client_ip=client_ip)
        try:
            db.commit()
        except Exception:
            db.rollback()
        log.warning("login failed", extra={"email": payload.email})
        raise_http(
            status.HTTP_401_UNAUTHORIZED,
            ErrorCodes.AUTH_INVALID_CREDENTIALS,
            "invalid credentials",
        )

    # Success: clear the failure counter, rotate session.
    clear_login_failures(db, email=payload.email)
    # SEC2-015 — session rotation on login.
    revoke_all_user_sessions(db, user.id)
    sess = create_session_row(db, user.id)
    user.last_login_at = utcnow()
    _set_session_cookie(response, sess.token)
    log.info("login", extra={"user_id": str(user.id)})
    return ok({"user": {"id": str(user.id), "email": user.email, "name": user.name}})


# Expose the lockout window in seconds for the 429 retry_after_seconds field.
LOCKOUT_WINDOW_SECONDS: int = 15 * 60


@router.post("/logout")
def logout(request: Request, response: Response, db: DbSession) -> Envelope[None]:
    cookie_name = settings().SESSION_COOKIE_NAME
    token = request.cookies.get(cookie_name)
    if token:
        revoke_session(db, token)
    response.delete_cookie(cookie_name, path="/")
    log.info("logout")
    return ok(None, "logged out")


@router.get("/me")
def me(user: CurrentUser, db: DbSession) -> Envelope[dict]:
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
