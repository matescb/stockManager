from __future__ import annotations

import hashlib
import hmac as _hmac
import secrets
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy import select

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
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace, WorkspaceInvitation, WorkspaceMember

router = APIRouter()


def _hash_token(plaintext: str) -> str:
    """SHA-256 hex digest of the plaintext token.

    Used only to populate `token_hash` at creation time (for backward
    compatibility — the unique index on `token_hash` still exists).
    Accept flow no longer queries by this value; it uses `_hmac_token`
    + `hmac.compare_digest` instead (SEC2-013).
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _hmac_token(plaintext: str) -> str:
    """HMAC-SHA-256 (keyed on SESSION_SECRET) hex digest of the plaintext.

    SEC2-013: stored as `token_hmac` on the row. The accept flow looks
    up by `id` (PK — no timing oracle on the SQL lookup) and then calls
    `hmac.compare_digest(_hmac_token(supplied), row.token_hmac)` so the
    comparison is constant-time regardless of the digest value.
    """
    key = settings().SESSION_SECRET.encode("utf-8")
    return _hmac.new(key, plaintext.encode("utf-8"), "sha256").hexdigest()


class InviteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: Literal["admin", "member", "viewer"] = "member"


def _serialize(inv: WorkspaceInvitation, *, plaintext_token: str | None = None) -> dict:
    """Serialise an invitation row.

    `plaintext_token` is set ONLY in the create response — the only
    moment the plaintext exists. Subsequent reads (list, revoke) never
    have it, because the DB stores only the hash. The caller is
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
        "created_at": inv.created_at.isoformat(),
        "accepted_at": inv.accepted_at.isoformat() if inv.accepted_at else None,
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
    # Already a member?
    existing_member = (
        db.execute(
            select(WorkspaceMember, User)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(WorkspaceMember.workspace_id == ws.id)
            .where(User.email == payload.email)
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
            .where(WorkspaceInvitation.email == payload.email)
            .where(WorkspaceInvitation.status == "pending")
        )
        .scalars()
        .first()
    )
    if existing:
        return ok(_serialize(existing))

    # Mint a new token. 32 bytes urlsafe ≈ 256 bits of entropy — well
    # past brute-force feasibility. Both the SHA-256 hash (for backward
    # compatibility / unique index) and the HMAC digest (SEC2-013, used
    # at accept time with compare_digest) go to the DB; the plaintext
    # goes back to the caller exactly once via the response.
    plaintext = secrets.token_urlsafe(32)
    inv = WorkspaceInvitation(
        workspace_id=ws.id,
        email=payload.email,
        role=payload.role,
        token_hash=_hash_token(plaintext),
        token_hmac=_hmac_token(plaintext),
        invited_by=user.id,
    )
    db.add(inv)
    # Flush so the Python-side `created_at = default=_utcnow` populates
    # before _serialize reads it. The dep commits on clean exit.
    db.flush()
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
def revoke_invitation(invitation_id: UUID, db: DbSession, ws: CurrentWorkspace):
    inv = db.get(WorkspaceInvitation, invitation_id)
    if not inv or inv.workspace_id != ws.id:
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
    return ok(None, "revoked")


# ---- Public accept endpoint (does NOT require workspace membership) ------


class AcceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # SEC2-013: the token field now carries a composite value of the form
    # "{invitation_id}:{plaintext_token}", produced by _serialize().  The
    # accept handler splits on the first ":" to obtain the PK (for the
    # DB lookup) and the plaintext (for HMAC comparison).  This keeps the
    # frontend interface to a single opaque string while allowing a
    # constant-time comparison path.
    token: str


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
    if inv.email.lower() != user.email.lower():
        raise_http(
            status.HTTP_403_FORBIDDEN,
            ErrorCodes.INVITATION_EMAIL_MISMATCH,
            "invitation is for a different email",
        )

    # Already a member?
    existing = (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.workspace_id == inv.workspace_id,
            WorkspaceMember.user_id == user.id,
        )
        .first()
    )
    if existing:
        existing.status = "active"
        existing.role = inv.role
    else:
        db.add(
            WorkspaceMember(
                workspace_id=inv.workspace_id,
                user_id=user.id,
                role=inv.role,
                status="active",
            )
        )
    inv.status = "accepted"
    inv.accepted_at = datetime.now(timezone.utc)
    inv.accepted_by = user.id
    ws = db.get(Workspace, inv.workspace_id)
    return ok({"workspace_id": str(inv.workspace_id), "workspace_name": ws.name if ws else None, "role": inv.role})
