from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import CurrentUser, CurrentWorkspace, DbSession, require_role
from app.core.errors import ErrorCodes, raise_http
from app.core.ratelimit import limiter
from app.core.responses import ok
from app.core.secrets import decrypt, encrypt
from app.domain.audit.service import log as _audit_log
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace, WorkspaceCatalogToken, WorkspaceMember
from app.domain.workspaces.schemas import (
    CatalogTokenIn,
    MemberPatch,
    WorkspaceCreateIn,
    WorkspacePatch,
)

router = APIRouter()

# Per-user owned-workspaces cap (BE2-004). Counts active rows where
# the user is the `owner_user_id`; the personal workspace minted at
# signup is `kind="personal"` and excluded so a user always has at
# least one tenant. Everything beyond it (extra organisations) counts.
_OWNED_ORG_WORKSPACE_CAP = 5


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
        raise_http(
            status.HTTP_409_CONFLICT,
            ErrorCodes.WORKSPACE_OWNER_CAP,
            "owned-workspace cap reached",
            existing_count=existing_count,
            cap=_OWNED_ORG_WORKSPACE_CAP,
        )
    ws = Workspace(name=payload.name, kind="organization", owner_user_id=user.id, currency_default=payload.currency_default)
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner", status="active"))
    return ok({"id": str(ws.id), "name": ws.name})


def _hmac_token(token: str) -> str:
    """Compute HMAC-SHA256 of *token* keyed by SESSION_SECRET."""
    secret = settings().SESSION_SECRET
    return hmac.new(
        secret.encode(),
        token.encode(),
        hashlib.sha256,
    ).hexdigest()


def _serialize_workspace(ws: Workspace, new_token: str | None = None) -> dict:
    """Serialize a workspace for API responses.

    SEC2-008: the plaintext catalog_token is no longer echoed in read
    responses — only `catalog_token_set: bool` is exposed so clients know
    whether a token exists without ever seeing it.

    When the caller supplies *new_token* (the freshly minted plaintext
    returned exactly once at regeneration time) it is included in the
    response as `catalog_token_plaintext`.  The frontend must show it
    once in a copy-once modal; subsequent GET /workspaces/current calls
    will NOT include it.
    """
    out: dict = {
        "id": str(ws.id),
        "name": ws.name,
        "kind": ws.kind,
        "currency_default": ws.currency_default,
        "lot_control_enabled": ws.lot_control_enabled,
        "serial_tracking_enabled": ws.serial_tracking_enabled,
        "catalog_enabled": bool(ws.catalog_enabled),
        # Only expose whether a token is set — never the plaintext or hash.
        "catalog_token_set": bool(ws.catalog_token_hash),
        "parts_provider": ws.parts_provider or "none",
        # Never echo the API key/secret. Just say whether each is set.
        "has_parts_provider_api_key": bool(ws.parts_provider_api_key),
        "has_parts_provider_api_secret": bool(ws.parts_provider_api_secret),
        "sourcing_provider": ws.sourcing_provider or "none",
        "sourcing_country_code": ws.sourcing_country_code,
        "sourcing_currency_code": ws.sourcing_currency_code,
        "sourcing_preferred_distributors": ws.sourcing_preferred_distributors,
        "sourcing_use_cached_for_dashboards": bool(
            ws.sourcing_use_cached_for_dashboards
        ),
        # Sourcing credentials follow the encrypted-at-rest parts-provider pattern.
        "has_sourcing_company_id": bool(ws.sourcing_company_id_enc),
        "has_sourcing_api_key": bool(ws.sourcing_api_key_enc),
        "scanner": ws.scanner or "zxing",
        # Same secret-handling pattern as the parts-provider key.
        "has_scanner_license_key": bool(ws.scanner_license_key),
    }
    if new_token is not None:
        # Shown once — the frontend must present a copy-once UI.
        out["catalog_token_plaintext"] = new_token
    return out


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


@router.patch("/current", dependencies=[Depends(require_role("admin"))])
def patch_current(payload: WorkspacePatch, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    data = payload.model_dump(exclude_unset=True)
    regenerate = bool(data.pop("regenerate_catalog_token", False))
    was_enabled = bool(ws.catalog_enabled)

    # Track which credential fields were changed so we can emit a
    # single audit row.  NEVER store the plaintext values in the audit
    # log — only the field names.
    _credential_fields_changed: list[str] = []

    # parts_provider_api_key / _api_secret need special handling so ''
    # actually clears (rather than being stored as an empty string).
    # All three credentials are encrypted at rest via app.core.secrets
    # (Sec HIGH-9). Empty string still clears (encrypt('') → None).
    if "parts_provider_api_key" in data:
        new_key = data.pop("parts_provider_api_key")
        ws.parts_provider_api_key = encrypt(new_key) if new_key else None
        _credential_fields_changed.append("parts_provider_api_key")
    if "parts_provider_api_secret" in data:
        new_secret = data.pop("parts_provider_api_secret")
        ws.parts_provider_api_secret = encrypt(new_secret) if new_secret else None
        _credential_fields_changed.append("parts_provider_api_secret")
    if "scanner_license_key" in data:
        new_license = data.pop("scanner_license_key")
        ws.scanner_license_key = encrypt(new_license) if new_license else None
        _credential_fields_changed.append("scanner_license_key")

    for k, v in data.items():
        setattr(ws, k, v)
    # Mint a token when enabling the catalog for the first time, or when the
    # caller explicitly asks for a fresh one while it's enabled.
    #
    # SEC2-008: the plaintext token is returned exactly once in the response
    # and is never stored.  Only the HMAC-SHA256 hash is persisted so the DB
    # column can't be exploited via a timing side-channel.
    new_token: str | None = None
    if ws.catalog_enabled and (regenerate or not ws.catalog_token_hash or not was_enabled):
        new_token = secrets.token_urlsafe(32)
        new_digest = _hmac_token(new_token)
        # Keep catalog_token / catalog_token_hash on Workspace for rollback
        # safety; the catalog router DOES NOT consult them at lookup time
        # (see app.api.routes.catalog._resolve_workspace) so they cannot
        # bypass the WorkspaceCatalogToken.revoked_at predicate.
        ws.catalog_token = new_token
        ws.catalog_token_hash = new_digest

        # Mirror the new token into workspace_catalog_tokens — the sole
        # auth source post-SEC2-019. Revoke any prior active "default"
        # rows (from a previous PATCH-mint or migration backfill) so a
        # mint never leaves two PATCH-minted tokens valid simultaneously.
        # User-labelled tokens minted via the /catalog/tokens endpoint
        # are NOT touched here.
        existing = db.execute(
            select(WorkspaceCatalogToken).where(
                WorkspaceCatalogToken.workspace_id == ws.id,
                WorkspaceCatalogToken.revoked_at.is_(None),
                WorkspaceCatalogToken.label.in_(
                    ("default", "default (legacy)")
                ),
            )
        ).scalars().all()
        now = datetime.now(timezone.utc)
        for row in existing:
            row.revoked_at = now
        db.add(
            WorkspaceCatalogToken(
                workspace_id=ws.id,
                token_hmac=new_digest,
                label="default",
                created_by_user_id=user.id,
            )
        )

    if _credential_fields_changed:
        # Audit credential rotation — ONLY field names, never values.
        _audit_log(
            db,
            ws=ws,
            user=user,
            action="workspace.credentials_rotated",
            target_type="workspace",
            target_ids=[ws.id],
            comment=f"fields={','.join(_credential_fields_changed)}",
        )

    return ok(_serialize_workspace(ws, new_token=new_token))


# ---------------------------------------------------------------------------
# Catalog token CRUD (SEC2-019 / issue #77)
# ---------------------------------------------------------------------------


def _serialize_catalog_token(t: WorkspaceCatalogToken, plaintext: str | None = None) -> dict:
    """Serialize a WorkspaceCatalogToken for API responses.

    NEVER includes token_hmac.  If plaintext is provided (only at creation
    time) it is included as `token` — visible once, then gone.
    """
    out: dict = {
        "id": str(t.id),
        "label": t.label,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
        "revoked_at": t.revoked_at.isoformat() if t.revoked_at else None,
    }
    if plaintext is not None:
        out["token"] = plaintext
    return out


@router.get(
    "/current/catalog/tokens",
    dependencies=[Depends(require_role("admin"))],
)
def list_catalog_tokens(db: DbSession, ws: CurrentWorkspace):
    """List all catalog tokens for the current workspace (admin+).

    Returns CatalogTokenOut list — never token_hmac or plaintext.
    """
    tokens = list(
        db.execute(
            select(WorkspaceCatalogToken)
            .where(WorkspaceCatalogToken.workspace_id == ws.id)
            .order_by(WorkspaceCatalogToken.created_at)
        ).scalars()
    )
    return ok([_serialize_catalog_token(t) for t in tokens])


@router.post(
    "/current/catalog/tokens",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin"))],
)
def create_catalog_token(
    payload: CatalogTokenIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    """Mint a new catalog token (admin+).

    Returns CatalogTokenCreatedOut — includes `token` plaintext ONCE.
    The plaintext is never stored; only the HMAC is persisted.
    """
    plaintext = secrets.token_urlsafe(32)
    digest = _hmac_token(plaintext)
    t = WorkspaceCatalogToken(
        workspace_id=ws.id,
        token_hmac=digest,
        label=payload.label,
        created_by_user_id=user.id,
    )
    db.add(t)
    db.flush()
    return ok(_serialize_catalog_token(t, plaintext=plaintext))


@router.delete(
    "/current/catalog/tokens/{token_id}",
    dependencies=[Depends(require_role("admin"))],
)
def revoke_catalog_token(token_id: UUID, db: DbSession, ws: CurrentWorkspace):
    """Revoke a catalog token (admin+).

    Sets revoked_at = now(). Cross-workspace access → 404 (never 403).
    """
    t = db.get(WorkspaceCatalogToken, token_id)
    if not t or t.workspace_id != ws.id:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            ErrorCodes.RESOURCE_NOT_FOUND,
            "catalog token not found",
        )
    if t.revoked_at is not None:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            ErrorCodes.RESOURCE_NOT_FOUND,
            "catalog token not found",
        )
    t.revoked_at = datetime.now(timezone.utc)
    return ok(_serialize_catalog_token(t))


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
        raise_http(
            status.HTTP_404_NOT_FOUND,
            ErrorCodes.WORKSPACE_MEMBER_NOT_FOUND,
            "member not found",
        )
    target_promotion_to_owner = payload.role == "owner"
    target_was_owner = m.role == "owner"
    if target_promotion_to_owner or target_was_owner:
        my_role = (
            db.query(WorkspaceMember)
            .filter(WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == user.id)
            .first()
        )
        if not my_role or my_role.role != "owner":
            raise_http(
                status.HTTP_403_FORBIDDEN,
                ErrorCodes.WORKSPACE_OWNER_ONLY,
                "only owners can manage owner role",
            )
    if target_was_owner and (payload.role and payload.role != "owner"):
        if _active_owner_count(db, ws.id) <= 1:
            raise_http(
                status.HTTP_400_BAD_REQUEST,
                ErrorCodes.WORKSPACE_LAST_OWNER,
                "cannot demote the last owner",
            )
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(m, k, v)
    return ok({"id": str(m.id), "role": m.role, "status": m.status})


@router.delete("/members/{member_id}", dependencies=[Depends(require_role("admin"))])
def remove_member(member_id: UUID, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    m = db.get(WorkspaceMember, member_id)
    if not m or m.workspace_id != ws.id:
        raise_http(
            status.HTTP_404_NOT_FOUND,
            ErrorCodes.WORKSPACE_MEMBER_NOT_FOUND,
            "member not found",
        )
    if m.user_id == user.id:
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            ErrorCodes.WORKSPACE_SELF_REMOVE,
            "cannot remove yourself; transfer ownership first",
        )
    if m.role == "owner" and _active_owner_count(db, ws.id) <= 1:
        raise_http(
            status.HTTP_400_BAD_REQUEST,
            ErrorCodes.WORKSPACE_LAST_OWNER,
            "cannot remove the last owner",
        )
    db.delete(m)
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
        raise_http(
            status.HTTP_404_NOT_FOUND,
            ErrorCodes.WORKSPACE_NOT_FOUND,
            "workspace not found",
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
