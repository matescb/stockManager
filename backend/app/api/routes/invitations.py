from __future__ import annotations

import hmac as _hmac
import secrets
from datetime import timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api._helpers import assert_in_workspace
from app.core.config import settings
from app.core.deps import (
    CurrentUser,
    CurrentWorkspace,
    DbSession,
    require_role,
)
from app.core.errors import ErrorCodes, raise_http
from app.core.ratelimit import limiter
from app.core.responses import ok
from app.core.time import utcnow
from app.domain.audit.service import log as _audit_log
from app.domain.users.models import User
from app.domain.workspaces.models import (
    Workspace,
    WorkspaceInvitation,
    WorkspaceMember,
    invitation_expires_at,
)
from app.domain.workspaces.schemas import AcceptIn, InviteIn

router = APIRouter()


def _iso_utc(value):
    return value.astimezone(timezone.utc).isoformat()


def _hmac_token(plaintext: str) -> str:
    """HMAC-SHA-256 (keyed on SESSION_SECRET) hex digest of the plaintext.

    SEC2-013: stored as `token_hmac` on the row. The accept flow looks
    up by `id` (PK — no timing oracle on the SQL lookup) and then calls
    `hmac.compare_digest(_hmac_token(supplied), row.token_hmac)` so the
    comparison is constant-time regardless of the digest value.
    """
    key = settings().SESSION_SECRET.encode("utf-8")
    return _hmac.new(key, plaintext.encode("utf-8"), "sha256").hexdigest()


def _serialize(inv: WorkspaceInvitation, *, plaintext_token: str | None = None) -> dict:
    """Serialise an invitation row.

    `plaintext_token` is set ONLY in the create response — the only
    moment the plaintext exists. Subsequent reads (list, revoke) never
    have it, because the DB stores only the keyed digest. The caller is
    responsible for delivering the plaintext to the invitee out-of-band
    immediately (typically via the link embedded in the response).

    SEC2-013: when a plaintext_token is provided the `token` field is
    returned as the composite string "{invitation_id}:{plaintext_token}".
    The accept endpoint splits this to obtain the PK for its DB lookup
    (no timing oracle) and the plaintext for HMAC comparison.
    """
    composite_token: str | None = None
    if plaintext_token and inv.status == "pending":
        composite_token = f"{inv.id}:{plaintext_token}"

    return {
        "id": str(inv.id),
        "workspace_id": str(inv.workspace_id),
        "email": inv.email,
        "role": inv.role,
        "status": inv.status,
        # Only the create response carries the composite token; all other
        # serialisations return None here.
        "token": composite_token,
        "created_at": _iso_utc(inv.created_at),
        "expires_at": _iso_utc(inv.expires_at),
        "accepted_at": _iso_utc(inv.accepted_at) if inv.accepted_at else None,
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
    # Email is case-insensitive per RFC 5321 §2.4. We normalise to lower
    # for both the membership/duplicate-pending lookups and the row
    # insert so the partial composite index landed in alembic 0020
    # (`lower(email) WHERE status = 'pending'`) actually gets used. The
    # admin signup UI already passes lowercased values through Pydantic's
    # EmailStr in practice, but the explicit `.lower()` here pins the
    # contract regardless of upstream input. DB-014 / issue #105.
    email = payload.email.lower()

    # Already a member? Compare via lower() because users.email is not
    # normalised at signup (Pydantic EmailStr does not lowercase the
    # local part), so a row stored as `Foo@Example.com` would otherwise
    # bypass the dedupe and we'd mint a duplicate invitation.
    existing_member = (
        db.execute(
            select(WorkspaceMember, User)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(WorkspaceMember.workspace_id == ws.id)
            .where(func.lower(User.email) == email)
        )
        .first()
    )
    if existing_member:
        raise_http(
            status.HTTP_409_CONFLICT,
            ErrorCodes.INVITATION_ALREADY_MEMBER,
            "user is already a member",
        )

    # Existing pending invite for this email — reuse rather than duplicate.
    # Note: we cannot return the existing plaintext token (we don't have
    # it). The frontend interprets a pending row with token=None as
    # "already invited; ask the operator to revoke + re-invite if the
    # invitee never received the original email".
    existing = (
        db.execute(
            select(WorkspaceInvitation)
            .where(WorkspaceInvitation.workspace_id == ws.id)
            .where(WorkspaceInvitation.email == email)
            .where(WorkspaceInvitation.status == "pending")
        )
        .scalars()
        .first()
    )
    if existing:
        return ok(_serialize(existing))

    # Mint a new token. 32 bytes urlsafe ≈ 256 bits of entropy — well
    # past brute-force feasibility. Only the HMAC digest (SEC2-013, used
    # at accept time with compare_digest) goes to the DB; the plaintext
    # goes back to the caller exactly once via the response.
    plaintext = secrets.token_urlsafe(32)
    # Cache workspace_id as a plain Python value before the savepoint so
    # we can query with it even if the session's object identity cache is
    # partially expired after a savepoint rollback (BE2-020 / #65).
    ws_id = ws.id
    inv = WorkspaceInvitation(
        workspace_id=ws_id,
        email=email,
        role=payload.role,
        token_hmac=_hmac_token(plaintext),
        invited_by=user.id,
        expires_at=invitation_expires_at(),
    )
    db.add(inv)
    # Wrap in a savepoint so an IntegrityError from concurrent creates
    # doesn't poison the outer transaction (BE2-020 / #65). If two
    # requests race past the existence check above, the second will hit
    # uq_workspace_invitation_pending and land here; we roll back the
    # savepoint and re-fetch the row the winner just inserted.
    #
    # SQLAlchemy 2.0 note: after IntegrityError exits begin_nested(),
    # the savepoint is rolled back, but the session marks itself as
    # needing a rollback (PendingRollbackError). We call db.rollback()
    # to restore the session to a clean state, then re-SELECT within a
    # fresh implicit transaction. The outer begin_nested() pattern used
    # in bulk_import differs because it never needs to continue using the
    # session after the catch.
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        # Rollback clears the PendingRollbackError state. This rolls
        # back the entire outer transaction, but since we haven't flushed
        # anything meaningful (the conflicting inv was rejected), the
        # only thing we lose is the duplicate row we were trying to add.
        db.rollback()
        # Re-SELECT the winning row using cached plain-Python IDs.
        existing = (
            db.execute(
                select(WorkspaceInvitation)
                .where(WorkspaceInvitation.workspace_id == ws_id)
                .where(WorkspaceInvitation.email == email)
                .where(WorkspaceInvitation.status == "pending")
            )
            .scalars()
            .first()
        )
        # If we can't find the row (shouldn't happen), fall through and
        # let the outer transaction fail naturally.
        if existing:
            return ok(_serialize(existing))
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="invitation.created",
        target_type="invitation",
        target_ids=[inv.id],
        comment=f"email={inv.email} role={inv.role}",
    )
    return ok(_serialize(inv, plaintext_token=plaintext))


@router.get("", dependencies=[Depends(require_role("admin"))])
def list_invitations(
    db: DbSession,
    ws: CurrentWorkspace,
    limit: int = Query(default=200, le=1000),
):
    rows = list(
        db.execute(
            select(WorkspaceInvitation)
            .where(WorkspaceInvitation.workspace_id == ws.id)
            .order_by(WorkspaceInvitation.created_at.desc())
            .limit(limit)
        ).scalars()
    )
    return ok([_serialize(r) for r in rows])


@router.delete("/{invitation_id}", dependencies=[Depends(require_role("admin"))])
def revoke_invitation(invitation_id: UUID, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    try:
        inv = assert_in_workspace(db, WorkspaceInvitation, invitation_id, ws.id, label="invitation")
    except HTTPException:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            ErrorCodes.INVITATION_NOT_FOUND,
            "invitation not found",
        )
    if inv.status != "pending":
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            ErrorCodes.INVITATION_NOT_PENDING,
            f"cannot revoke a {inv.status} invitation",
            invitation_status=inv.status,
        )
    inv.status = "revoked"
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="invitation.revoked",
        target_type="invitation",
        target_ids=[inv.id],
        comment=f"email={inv.email}",
    )
    return ok(None, "revoked")


# ---- Public accept endpoint (does NOT require workspace membership) ------


# Token is 256-bit so brute-force is infeasible, but the endpoint is
# unauthenticated-by-workspace and could otherwise be hammered.
# 10/min/IP is well above any legitimate user's behaviour and well
# below useful enumeration speed.
@router.post("/accept")
@limiter.limit("10/minute")
def accept_invitation(request: Request, payload: AcceptIn, db: DbSession, user: CurrentUser):
    # SEC2-013: the composite token encodes "{invitation_id}:{plaintext}".
    # Split on the first ":" only — the plaintext may theoretically
    # contain ":" characters (urlsafe_b64 doesn't, but be defensive).
    parts = payload.token.split(":", 1)
    if len(parts) != 2:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            ErrorCodes.INVITATION_NOT_FOUND,
            "invitation not found",
        )
    raw_id, plaintext = parts
    try:
        invitation_id = UUID(raw_id)
    except ValueError:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            ErrorCodes.INVITATION_NOT_FOUND,
            "invitation not found",
        )

    # Look up by id (PK) — no timing oracle on the SQL side.
    inv = db.get(WorkspaceInvitation, invitation_id)

    # Compute the HMAC of the supplied plaintext before branching on whether
    # the row exists or whether the HMAC matches.  This ensures we do the
    # same amount of crypto work on every code path (avoids short-circuit
    # timing leak on missing-row vs wrong-token).
    supplied_hmac = _hmac_token(plaintext)

    token_ok = (
        inv is not None
        and inv.token_hmac is not None
        and _hmac.compare_digest(supplied_hmac, inv.token_hmac)
    )
    if not token_ok:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            ErrorCodes.INVITATION_NOT_FOUND,
            "invitation not found",
        )
    if inv.status != "pending":
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            ErrorCodes.INVITATION_NOT_PENDING,
            f"invitation is {inv.status}",
            invitation_status=inv.status,
        )
    if inv.expires_at <= utcnow():
        raise_http(
            status.HTTP_410_GONE,
            ErrorCodes.INVITATION_EXPIRED,
            "invitation has expired",
        )
    if inv.email.lower() != user.email.lower():
        raise_http(
            status.HTTP_403_FORBIDDEN,
            ErrorCodes.INVITATION_EMAIL_MISMATCH,
            "invitation is for a different email",
        )

    # Cache plain-Python values before the savepoint so we can query
    # with them even if session identity-map objects are partially
    # invalidated after a savepoint rollback (BE2-020 / #65).
    inv_workspace_id = inv.workspace_id
    inv_role = inv.role
    user_id = user.id

    # Already a member?
    existing_member = (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.workspace_id == inv_workspace_id,
            WorkspaceMember.user_id == user_id,
        )
        .first()
    )
    if existing_member:
        existing_member.status = "active"
        existing_member.role = inv_role
    else:
        new_member = WorkspaceMember(
            workspace_id=inv_workspace_id,
            user_id=user_id,
            role=inv_role,
            status="active",
        )
        db.add(new_member)
    inv.status = "accepted"
    inv.accepted_at = utcnow()
    inv.accepted_by = user_id

    # Wrap the membership insert in a savepoint so that two concurrent
    # accepts for the same token both succeed (BE2-020 / #65). If two
    # requests race past the membership existence check above, the second
    # will hit uq_workspace_member on flush. Roll back the savepoint,
    # re-fetch the existing member row and re-apply role/status — the
    # net result is idempotent (the user is active in the workspace).
    #
    # SQLAlchemy 2.0 note: after IntegrityError exits begin_nested(),
    # the session marks itself as needing a full rollback. We call
    # db.rollback() to restore a clean state, then re-apply writes.
    inv_id = inv.id  # cache before rollback discards ORM identity
    inv_email = inv.email  # cache before possible rollback
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        db.rollback()
        # Re-fetch the member that the winning concurrent request created,
        # and ensure it has the right role/status.
        colliding_member = (
            db.query(WorkspaceMember)
            .filter(
                WorkspaceMember.workspace_id == inv_workspace_id,
                WorkspaceMember.user_id == user_id,
            )
            .first()
        )
        if colliding_member:
            colliding_member.status = "active"
            colliding_member.role = inv_role
        # The invitation status update was rolled back too; re-apply.
        fresh_inv = db.get(WorkspaceInvitation, inv_id)
        if fresh_inv and fresh_inv.status != "accepted":
            fresh_inv.status = "accepted"
            fresh_inv.accepted_at = utcnow()
            fresh_inv.accepted_by = user_id

    workspace = db.get(Workspace, inv_workspace_id)
    if workspace:
        _audit_log(
            db,
            ws=workspace,
            user=user,
            action="invitation.accepted",
            target_type="invitation",
            target_ids=[inv_id],
            comment=f"email={inv_email} role={inv_role}",
        )
    return ok(
        {
            "workspace_id": str(inv_workspace_id),
            "workspace_name": workspace.name if workspace else None,
            "role": inv_role,
        }
    )
