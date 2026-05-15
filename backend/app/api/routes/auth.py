from __future__ import annotations

import hashlib
import hmac as _hmac
import secrets
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import func, text

from app.core.auth import (
    PasswordVerifyResult,
    WeakPasswordError,
    check_login_lockout,
    clear_login_failures,
    create_session_row,
    hash_password,
    hash_session_token,
    hmac_token,
    mint_password_reset_token,
    record_login_failure,
    revoke_all_user_sessions,
    revoke_session,
    validate_password_strength,
    verify_password_with_rehash,
)
from app.core.auth import (
    verify_password as _verify_password,
)
from app.core.config import settings
from app.core.cookies import (
    WORKSPACE_COOKIE_NAME,
    delete_session_cookie,
    delete_workspace_cookie,
    session_cookie_attrs,
)
from app.core.deps import CurrentUser, DbSession
from app.core.errors import ErrorCodes, raise_http
from app.core.logging import get_logger
from app.core.mail import (
    send_account_exists_email,
    send_password_reset_email,
    send_verification_email,
)
from app.core.ratelimit import limiter
from app.core.responses import Envelope, ok
from app.core.time import utcnow
from app.domain.audit.service import log as _audit_log
from app.domain.users.models import PasswordResetRequest, PendingUser, User, UserSession
from app.domain.users.schemas import (
    LoginIn,
    PasswordResetIn,
    RequestPasswordResetIn,
    SignupIn,
    VerifyIn,
)
from app.domain.workspaces.models import Workspace, WorkspaceMember

router = APIRouter()
log = get_logger(__name__)

# How long a pending signup verification is valid (in hours).
_VERIFY_TTL_HOURS = 24
_PASSWORD_RESET_TTL_HOURS = 1
_PASSWORD_RESET_EMAIL_LIMIT = 3
_DUMMY_ARGON2 = (
    "$argon2id$v=19$m=65536,t=3,p=4$"
    "yEZFbIMmBabse2MdUks7RA$Iggj8Dn26NU39IQQb7Vs8ADgvJayYRb194wtzFzGsF0"
)
verify_password = _verify_password


def _verify_password_for_login(hash_: str, password: str) -> PasswordVerifyResult:
    if verify_password is _verify_password:
        return verify_password_with_rehash(hash_, password)
    return PasswordVerifyResult(valid=verify_password(hash_, password))


_SIGNUP_VERIFICATION_DATA = {"status": "verification_sent"}
_SIGNUP_VERIFICATION_MESSAGE = "verification email sent"
_PASSWORD_RESET_REQUEST_DATA = {"status": "accepted"}
_PASSWORD_RESET_REQUEST_MESSAGE = "password reset request accepted"


def _record_signup_mail_failure(kind: str, exc: Exception) -> None:
    log.exception(
        "signup mail send failed",
        extra={"mail_kind": kind, "error_code": "mail.send_failed"},
    )
    try:
        import sentry_sdk

        sentry_sdk.capture_exception(exc)
    except Exception:
        pass


def _record_password_reset_mail_failure(exc: Exception) -> None:
    log.exception(
        "password reset mail send failed",
        extra={"mail_kind": "password_reset", "error_code": "mail.send_failed"},
    )
    try:
        import sentry_sdk

        sentry_sdk.capture_exception(exc)
    except Exception:
        pass


def _hash_email_for_password_reset(email: str) -> str:
    return hashlib.sha256(email.lower().encode("utf-8")).hexdigest()


def _dummy_password_reset_compute() -> None:
    # Keep known and unknown request paths close enough that the request
    # endpoint does not become an email-enumeration timing oracle.
    hash_password(secrets.token_urlsafe(24))


def _reap_expired_pending_signup(db: DbSession, email: str) -> None:
    cutoff = utcnow() - timedelta(hours=_VERIFY_TTL_HOURS)
    db.query(PendingUser).filter(
        PendingUser.email == email,
        PendingUser.created_at < cutoff,
        PendingUser.verified_at.is_(None),
    ).delete(synchronize_session=False)


def _active_pending_signup(db: DbSession, email: str) -> PendingUser | None:
    return (
        db.query(PendingUser)
        .filter(
            PendingUser.email == email,
            PendingUser.verified_at.is_(None),
        )
        .first()
    )


def _mint_signup_verification_material(password: str) -> tuple[str, str, str]:
    plaintext_token = secrets.token_urlsafe(32)
    password_hash = hash_password(password)
    verification_token_hmac = _hmac_token(plaintext_token)
    return plaintext_token, password_hash, verification_token_hmac


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings().SESSION_COOKIE_NAME,
        value=token,
        max_age=settings().SESSION_LIFETIME_DAYS * 24 * 3600,
        **session_cookie_attrs(),
    )


def _hmac_token(plaintext: str) -> str:
    """HMAC-SHA-256 (keyed on SESSION_SECRET) hex digest of the plaintext.

    Used for the email-verification token stored in `pending_users.
    verification_token_hmac`.  The accept flow compares via
    `hmac.compare_digest(_hmac_token(supplied), row.verification_token_hmac)`
    for constant-time comparison (SEC2-013 pattern).
    """
    return hmac_token(plaintext)


def _first_workspace_for_user(db: DbSession, user: User) -> Workspace | None:
    membership = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.user_id == user.id, WorkspaceMember.status == "active")
        .order_by(WorkspaceMember.created_at.asc())
        .first()
    )
    return db.get(Workspace, membership.workspace_id) if membership else None


def _password_reset_request_throttled(db: DbSession, *, email_hash: str) -> bool:
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"reset:{email_hash}"},
    )
    cutoff = utcnow() - timedelta(hours=1)
    count = (
        db.query(PasswordResetRequest)
        .filter(
            PasswordResetRequest.email_hash == email_hash,
            PasswordResetRequest.created_at >= cutoff,
        )
        .count()
    )
    return count >= _PASSWORD_RESET_EMAIL_LIMIT


def _audit_password_reset_request(
    db: DbSession,
    request: Request,
    user: User,
    *,
    throttled: bool,
) -> None:
    workspace = _first_workspace_for_user(db, user)
    if workspace is None:
        return
    _audit_log(
        db,
        ws=workspace,
        user=user,
        action="user.password_reset_requested",
        target_type="user",
        target_ids=[user.id],
        comment="throttled" if throttled else None,
        request_id=getattr(request.state, "request_id", None),
    )


def _workspace_for_logout_audit(db, request: Request, user: User) -> Workspace | None:
    raw = request.headers.get("X-Workspace-Id") or request.cookies.get(WORKSPACE_COOKIE_NAME)
    if raw:
        try:
            workspace_id = UUID(raw)
        except ValueError:
            workspace_id = None
        if workspace_id:
            membership = (
                db.query(WorkspaceMember)
                .filter(
                    WorkspaceMember.user_id == user.id,
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.status == "active",
                )
                .first()
            )
            if membership:
                return db.get(Workspace, workspace_id)

    membership = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.user_id == user.id, WorkspaceMember.status == "active")
        .order_by(WorkspaceMember.created_at.asc())
        .first()
    )
    return db.get(Workspace, membership.workspace_id) if membership else None


def _logout_audit_context(db, request: Request, token: str) -> tuple[User, Workspace] | None:
    digest = hash_session_token(token)
    session = db.get(UserSession, digest)
    if session is None:
        return None
    user = db.get(User, session.user_id)
    if user is None:
        return None
    workspace = _workspace_for_logout_audit(db, request, user)
    if workspace is None:
        return None
    return user, workspace


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

    existing = db.query(User).filter(User.email == payload.email).first()
    if existing and not settings().SIGNUP_REQUIRE_EMAIL_VERIFICATION:
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
            {
                "user": {"id": str(user.id), "email": user.email, "name": user.name},
                "workspace_id": str(ws.id),
            },
        )

    # --- Prod path: email-verification two-step flow ---
    # Keep the known-email and fresh unknown-email compute paths close
    # enough that the response timing does not become the enumeration
    # signal after the response body was equalised. Both branches do the
    # pending-row maintenance and Argon2/HMAC token work; only the fresh
    # unknown-email branch persists the PendingUser and sends the verify
    # link.
    _reap_expired_pending_signup(db, payload.email)
    existing_pending = _active_pending_signup(db, payload.email)

    if existing:
        _mint_signup_verification_material(payload.password)
        try:
            send_account_exists_email(to=payload.email)
        except Exception as exc:
            _record_signup_mail_failure("duplicate_signup", exc)
        log.info("signup existing account notice sent", extra={"user_id": str(existing.id)})
        response.status_code = status.HTTP_202_ACCEPTED
        return ok(_SIGNUP_VERIFICATION_DATA, _SIGNUP_VERIFICATION_MESSAGE)

    # If there's already a non-expired pending row, return 202 again
    # without creating a duplicate row. The user may click the first
    # link or wait for it to expire and re-sign-up.
    if existing_pending:
        log.info("signup resent existing pending", extra={"email": payload.email})
        response.status_code = status.HTTP_202_ACCEPTED
        return ok(_SIGNUP_VERIFICATION_DATA, _SIGNUP_VERIFICATION_MESSAGE)

    # Mint a verification token, store its HMAC, send the link.
    plaintext_token, password_hash, verification_token_hmac = _mint_signup_verification_material(
        payload.password
    )
    pending = PendingUser(
        email=payload.email,
        name=payload.name,
        password_hash=password_hash,
        workspace_name=payload.workspace_name,
        verification_token_hmac=verification_token_hmac,
        ip=request.client.host if request.client else None,
    )
    db.add(pending)
    db.flush()  # populate pending.id

    # Build verification link and send the email.
    link = f"{settings().APP_BASE_URL}/verify?id={pending.id}&token={plaintext_token}"
    try:
        send_verification_email(to=payload.email, verification_link=link)
    except Exception as exc:
        _record_signup_mail_failure("verification", exc)

    log.info("signup pending", extra={"pending_id": str(pending.id)})
    response.status_code = status.HTTP_202_ACCEPTED
    return ok(_SIGNUP_VERIFICATION_DATA, _SIGNUP_VERIFICATION_MESSAGE)


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

    revoke_all_user_sessions(db, user.id)
    sess = create_session_row(db, user.id)
    user.last_login_at = utcnow()

    _set_session_cookie(response, sess.token)
    log.info(
        "signup verified",
        extra={"user_id": str(user.id), "workspace_id": str(ws.id)},
    )
    return ok(
        {
            "user": {"id": str(user.id), "email": user.email, "name": user.name},
            "workspace_id": str(ws.id),
        },
        "email verified",
    )


# ---------------------------------------------------------------------------
# Password reset — pre-auth request + single-use email token
# ---------------------------------------------------------------------------


@router.post("/request-password-reset")
@limiter.limit("10/hour")
def request_password_reset(
    request: Request,
    payload: RequestPasswordResetIn,
    response: Response,
    db: DbSession,
) -> Envelope[dict]:
    email = str(payload.email).strip()
    email_normalized = email.lower()
    email_hash = _hash_email_for_password_reset(email_normalized)
    client_ip = request.client.host if request.client else None
    now = utcnow()

    _dummy_password_reset_compute()
    user = db.query(User).filter(func.lower(User.email) == email_normalized).first()
    throttled = _password_reset_request_throttled(db, email_hash=email_hash)

    if user is None or getattr(user, "archived_at", None) is not None:
        response.status_code = status.HTTP_202_ACCEPTED
        return ok(_PASSWORD_RESET_REQUEST_DATA, _PASSWORD_RESET_REQUEST_MESSAGE)

    reset_request = PasswordResetRequest(
        user_id=user.id,
        email_hash=email_hash,
        ip=client_ip,
    )

    if not throttled:
        token, token_hmac = mint_password_reset_token()
        reset_request.token_hmac = token_hmac
        reset_request.expires_at = now + timedelta(hours=_PASSWORD_RESET_TTL_HOURS)
        db.add(reset_request)
        db.flush()

        link = f"{settings().APP_BASE_URL}/auth/reset-password?token={token}"
        try:
            send_password_reset_email(to=user.email, reset_link=link)
            reset_request.sent_at = now
        except Exception as exc:
            _record_password_reset_mail_failure(exc)
    else:
        db.add(reset_request)
        db.flush()

    _audit_password_reset_request(db, request, user, throttled=throttled)

    response.status_code = status.HTTP_202_ACCEPTED
    return ok(_PASSWORD_RESET_REQUEST_DATA, _PASSWORD_RESET_REQUEST_MESSAGE)


@router.post("/reset-password")
@limiter.limit("10/minute")
def reset_password(
    request: Request,
    payload: PasswordResetIn,
    db: DbSession,
) -> Envelope[dict]:
    token_hmac = hmac_token(payload.token)
    reset_request = (
        db.query(PasswordResetRequest)
        .filter(PasswordResetRequest.token_hmac == token_hmac)
        .with_for_update()
        .first()
    )
    if reset_request is None:
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            ErrorCodes.AUTH_RESET_INVALID,
            "invalid password reset link",
        )
    if reset_request.used_at is not None:
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            ErrorCodes.AUTH_RESET_USED,
            "password reset link already used",
        )
    if reset_request.expires_at is None or reset_request.expires_at < utcnow():
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            ErrorCodes.AUTH_RESET_EXPIRED,
            "password reset link expired",
        )

    user = db.get(User, reset_request.user_id) if reset_request.user_id else None
    if user is None or getattr(user, "archived_at", None) is not None:
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            ErrorCodes.AUTH_RESET_INVALID,
            "invalid password reset link",
        )

    try:
        validate_password_strength(payload.new_password)
    except WeakPasswordError as exc:
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            ErrorCodes.AUTH_WEAK_PASSWORD,
            str(exc),
        )

    reset_request.used_at = utcnow()
    user.password_hash = hash_password(payload.new_password)
    revoked_sessions = revoke_all_user_sessions(db, user.id)
    clear_login_failures(db, email=user.email)

    workspace = _first_workspace_for_user(db, user)
    if workspace is not None:
        _audit_log(
            db,
            ws=workspace,
            user=user,
            action="user.password_reset",
            target_type="user",
            target_ids=[user.id],
            request_id=getattr(request.state, "request_id", None),
        )

    return ok(
        {"status": "password_reset", "revoked_sessions": revoked_sessions},
        "password reset",
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
    password_hash = user.password_hash if user is not None else _DUMMY_ARGON2
    password_result = _verify_password_for_login(password_hash, payload.password)
    if user is None or not password_result.valid:
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
    if password_result.rehash is not None:
        user.password_hash = password_result.rehash
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
        audit_context = _logout_audit_context(db, request, token)
        if audit_context is not None:
            user, workspace = audit_context
            _audit_log(
                db,
                ws=workspace,
                user=user,
                action="user.logout",
                target_type="user",
                target_ids=[user.id],
                request_id=getattr(request.state, "request_id", None),
            )
        revoke_session(db, token)
    delete_session_cookie(response, cookie_name)
    delete_workspace_cookie(response)
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
