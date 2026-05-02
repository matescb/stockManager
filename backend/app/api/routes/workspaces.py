from __future__ import annotations

import secrets
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession, require_role
from app.core.ratelimit import limiter
from app.core.responses import ok
from app.core.secrets import decrypt, encrypt
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace, WorkspaceMember

router = APIRouter()

# Per-user owned-workspaces cap (BE2-004). Counts active rows where
# the user is the `owner_user_id`; the personal workspace minted at
# signup is `kind="personal"` and excluded so a user always has at
# least one tenant. Everything beyond it (extra organisations) counts.
_OWNED_ORG_WORKSPACE_CAP = 5


class WorkspaceCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
@limiter.limit("10/hour")
def create_workspace(
    request: Request,
    payload: WorkspaceCreateIn,
    user: CurrentUser,
    db: DbSession,
):
    # BE2-004 — cap per-user owned organisation workspaces. Without
    # this, an authenticated user can mint unbounded workspaces and
    # exhaust catalog tokens / report rows / storage. The personal
    # workspace from signup (`kind="personal"`) is excluded so the
    # cap is on extra orgs, not on the baseline tenant.
    existing_count = (
        db.query(WorkspaceMember)
        .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
        .filter(
            Workspace.owner_user_id == user.id,
            Workspace.kind == "organization",
            WorkspaceMember.user_id == user.id,
        )
        .count()
    )
    if existing_count >= _OWNED_ORG_WORKSPACE_CAP:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "owned-workspace cap reached",
                "existing_count": existing_count,
                "cap": _OWNED_ORG_WORKSPACE_CAP,
            },
        )
    ws = Workspace(name=payload.name, kind="organization", owner_user_id=user.id, currency_default=payload.currency_default)
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner", status="active"))
    db.commit()
    return ok({"id": str(ws.id), "name": ws.name})


def _catalog_url(ws: Workspace) -> str | None:
    if ws.catalog_enabled and ws.catalog_token:
        return f"/catalog/{ws.catalog_token}"
    return None


def _serialize_workspace(ws: Workspace) -> dict:
    return {
        "id": str(ws.id),
        "name": ws.name,
        "kind": ws.kind,
        "currency_default": ws.currency_default,
        "lot_control_enabled": ws.lot_control_enabled,
        "serial_tracking_enabled": ws.serial_tracking_enabled,
        "catalog_enabled": bool(ws.catalog_enabled),
        "catalog_url": _catalog_url(ws),
        "parts_provider": ws.parts_provider or "none",
        # Never echo the API key/secret. Just say whether each is set.
        "has_parts_provider_api_key": bool(ws.parts_provider_api_key),
        "has_parts_provider_api_secret": bool(ws.parts_provider_api_secret),
        "scanner": ws.scanner or "zxing",
        # Same secret-handling pattern as the parts-provider key.
        "has_scanner_license_key": bool(ws.scanner_license_key),
    }


@router.get("/current")
def current(ws: CurrentWorkspace):
    return ok(_serialize_workspace(ws))


@router.get(
    "/current/scanner-license-key",
    dependencies=[Depends(require_role("member"))],
)
def current_scanner_license_key(ws: CurrentWorkspace):
    """Raw Scandit license key for the scanner mount. Kept on a dedicated
    route so it never leaks into normal /current payloads — the regular
    serializer only emits has_scanner_license_key.

    Gated at member+ (SEC2-012 / BE2-017). Viewers can still read
    workspace settings via /current; they just can't pull the SDK key,
    which is a paid third-party credential and effectively a write
    capability for the scanner integration."""
    # Decrypt at the boundary (Sec HIGH-9). Column stores Fernet
    # ciphertext post-0016; SDK gets plaintext.
    return ok({"license_key": decrypt(ws.scanner_license_key) or ""})


class WorkspacePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    currency_default: str | None = Field(default=None, min_length=3, max_length=3)
    lot_control_enabled: bool | None = None
    serial_tracking_enabled: bool | None = None
    catalog_enabled: bool | None = None
    # Write-only command flag: when true (and the catalog stays enabled), the
    # route mints a fresh secrets.token_urlsafe(32) and stores it.
    regenerate_catalog_token: bool | None = None
    parts_provider: Literal["none", "mouser", "digikey"] | None = None
    # Empty string clears the stored key; any other non-None value replaces it.
    # None (omitted) leaves whatever's already stored alone.
    parts_provider_api_key: str | None = None
    # Same semantics as parts_provider_api_key. Used as DigiKey's
    # client_secret; Mouser doesn't need it.
    parts_provider_api_secret: str | None = None
    scanner: Literal["zxing", "scandit"] | None = None
    # Same '' clears / non-empty replaces / None leaves alone semantics.
    scanner_license_key: str | None = None


@router.patch("/current", dependencies=[Depends(require_role("admin"))])
def patch_current(payload: WorkspacePatch, db: DbSession, ws: CurrentWorkspace):
    data = payload.model_dump(exclude_unset=True)
    regenerate = bool(data.pop("regenerate_catalog_token", False))
    was_enabled = bool(ws.catalog_enabled)

    # parts_provider_api_key / _api_secret need special handling so ''
    # actually clears (rather than being stored as an empty string).
    # All three credentials are encrypted at rest via app.core.secrets
    # (Sec HIGH-9). Empty string still clears (encrypt('') → None).
    if "parts_provider_api_key" in data:
        new_key = data.pop("parts_provider_api_key")
        ws.parts_provider_api_key = encrypt(new_key) if new_key else None
    if "parts_provider_api_secret" in data:
        new_secret = data.pop("parts_provider_api_secret")
        ws.parts_provider_api_secret = encrypt(new_secret) if new_secret else None
    if "scanner_license_key" in data:
        new_license = data.pop("scanner_license_key")
        ws.scanner_license_key = encrypt(new_license) if new_license else None

    for k, v in data.items():
        setattr(ws, k, v)
    # Mint a token when enabling the catalog for the first time, or when the
    # caller explicitly asks for a fresh one while it's enabled.
    if ws.catalog_enabled and (regenerate or not ws.catalog_token or not was_enabled):
        ws.catalog_token = secrets.token_urlsafe(32)
    db.commit()
    return ok(_serialize_workspace(ws))


@router.get("/members")
def list_members(db: DbSession, ws: CurrentWorkspace):
    rows = list(
        db.execute(
            select(WorkspaceMember, User)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(WorkspaceMember.workspace_id == ws.id)
            .order_by(User.name)
        )
    )
    return ok(
        [
            {
                "id": str(m.id),
                "user_id": str(u.id),
                "email": u.email,
                "name": u.name,
                "role": m.role,
                "status": m.status,
            }
            for m, u in rows
        ]
    )


class MemberPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["owner", "admin", "member", "viewer"] | None = None
    status: Literal["active", "disabled"] | None = None


def _active_owner_count(db, ws_id):
    return len(
        db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == ws_id,
                WorkspaceMember.role == "owner",
                WorkspaceMember.status == "active",
            )
        ).scalars().all()
    )


@router.patch("/members/{member_id}", dependencies=[Depends(require_role("admin"))])
def patch_member(member_id: UUID, payload: MemberPatch, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    m = db.get(WorkspaceMember, member_id)
    if not m or m.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="member not found")
    target_promotion_to_owner = payload.role == "owner"
    target_was_owner = m.role == "owner"
    if target_promotion_to_owner or target_was_owner:
        my_role = (
            db.query(WorkspaceMember)
            .filter(WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == user.id)
            .first()
        )
        if not my_role or my_role.role != "owner":
            raise HTTPException(status_code=403, detail="only owners can manage owner role")
    if target_was_owner and (payload.role and payload.role != "owner"):
        if _active_owner_count(db, ws.id) <= 1:
            raise HTTPException(status_code=400, detail="cannot demote the last owner")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(m, k, v)
    db.commit()
    return ok({"id": str(m.id), "role": m.role, "status": m.status})


@router.delete("/members/{member_id}", dependencies=[Depends(require_role("admin"))])
def remove_member(member_id: UUID, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    m = db.get(WorkspaceMember, member_id)
    if not m or m.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="member not found")
    if m.user_id == user.id:
        raise HTTPException(status_code=400, detail="cannot remove yourself; transfer ownership first")
    if m.role == "owner" and _active_owner_count(db, ws.id) <= 1:
        raise HTTPException(status_code=400, detail="cannot remove the last owner")
    db.delete(m)
    db.commit()
    return ok(None, "removed")


@router.post("/{workspace_id}/switch")
def switch_workspace(
    workspace_id: UUID,
    response: Response,
    user: CurrentUser,
    db: DbSession,
):
    """SEC2-004 — switch the active workspace cookie.

    Pre-fix this route accepted any string as `workspace_id`, didn't
    look up membership, and didn't even require the caller to be
    authenticated. An attacker could craft a POST that would land any
    arbitrary string in the victim's cookie, breaking subsequent
    requests or tricking them into a workspace they don't belong to.

    Now: typed UUID, requires `CurrentUser`, and 404s unless the user
    has an active membership in the target workspace.
    """
    membership = (
        db.query(WorkspaceMember)
        .filter(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user.id,
            WorkspaceMember.status == "active",
        )
        .first()
    )
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="workspace not found",
        )

    # Hardened cookie attributes:
    # - httponly: the SPA reads the active workspace from localStorage,
    #   never from this cookie, so JS access is unnecessary and an XSS
    #   vector.
    # - secure in prod: cookie may not be sent over plain HTTP. Dev
    #   runs over HTTP so we keep it permissive there.
    # - samesite=strict (v1 Sec CRIT-4): the cookie is purely
    #   server-driven, never depended on by a cross-site flow. Lax
    #   would still be sent on top-level navigations, leaving a
    #   theoretical surface for a forced-switch via a victim clicking
    #   an attacker link. Strict closes that gap; the session cookie
    #   stays Lax because login-like top-level redirects do depend
    #   on it.
    response.set_cookie(
        key="stockmgr_workspace",
        value=str(workspace_id),
        httponly=True,
        secure=settings().APP_ENV == "prod",
        samesite="strict",
        max_age=365 * 24 * 3600,
        path="/",
    )
    return ok({"workspace_id": str(workspace_id)})
