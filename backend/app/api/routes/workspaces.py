from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select

from app.api._helpers import assert_in_workspace
from app.core.config import settings
from app.core.cookies import WORKSPACE_COOKIE_NAME, workspace_cookie_attrs
from app.core.deps import (
    CurrentUser,
    CurrentWorkspace,
    DbSession,
    api_token_workspace_id,
    forbid_api_token,
    require_role,
)
from app.core.errors import ErrorCodes, raise_http
from app.core.ratelimit import limiter, workspace_key
from app.core.responses import ok
from app.core.secrets import decrypt, encrypt
from app.domain.audit.service import log as _audit_log
from app.domain.parts.provider_credentials import (
    active_credential_rows,
    serialize_credential,
)
from app.domain.parts.provider_credentials import (
    upsert as _upsert_provider_credentials,
)
from app.domain.sourcing import cache as sourcing_cache
from app.domain.tokens import service as tokens_service
from app.domain.users.models import User
from app.domain.workspaces.master_lists import (
    ALL_COUNTRIES,
    ALL_CURRENCIES,
    ALL_DISTRIBUTORS,
)
from app.domain.workspaces.models import Workspace, WorkspaceCatalogToken, WorkspaceMember
from app.domain.workspaces.schemas import (
    CatalogTokenIn,
    MemberPatch,
    ProviderCredentialsIn,
    WorkspaceCreateIn,
    WorkspacePatch,
)

router = APIRouter()

# Per-user owned-workspaces cap (BE2-004). Counts active rows where
# the user is the `owner_user_id`; the personal workspace minted at
# signup is `kind="personal"` and excluded so a user always has at
# least one tenant. Everything beyond it (extra organisations) counts.
_OWNED_ORG_WORKSPACE_CAP = 5


def _encrypt_patch_credential(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.strip():
        return None
    return encrypt(value)


def _normalize_sourcing_code(data: dict, key: str, current: str | None) -> str | None:
    value = data.get(key, current)
    if isinstance(value, str):
        normalized = value.upper()
        if key in data:
            data[key] = normalized
        return normalized
    return value


def _validate_sourcing_code(
    code: str | None,
    active_values: list[str] | None,
    *,
    error_code: str,
    field: str,
    active_field: str,
) -> None:
    if not code:
        return
    if code not in (active_values or []):
        raise_http(
            422,
            error_code,
            f"{field} must be active for this workspace",
            field=field,
            active_field=active_field,
            value=code,
        )


def _validate_sourcing_defaults_against_active_lists(data: dict, ws: Workspace) -> None:
    watched_fields = {
        "sourcing_country_code",
        "sourcing_currency_code",
        "active_countries",
        "active_currencies",
    }
    if not watched_fields.intersection(data):
        return

    country_code = _normalize_sourcing_code(data, "sourcing_country_code", ws.sourcing_country_code)
    currency_code = _normalize_sourcing_code(
        data,
        "sourcing_currency_code",
        ws.sourcing_currency_code,
    )
    active_countries = data.get("active_countries", ws.active_countries)
    active_currencies = data.get("active_currencies", ws.active_currencies)

    _validate_sourcing_code(
        country_code,
        active_countries,
        error_code=ErrorCodes.SOURCING_INVALID_COUNTRY_CODE,
        field="sourcing_country_code",
        active_field="active_countries",
    )
    _validate_sourcing_code(
        currency_code,
        active_currencies,
        error_code=ErrorCodes.SOURCING_INVALID_CURRENCY_CODE,
        field="sourcing_currency_code",
        active_field="active_currencies",
    )


@router.get("")
def list_workspaces(request: Request, user: CurrentUser, db: DbSession):
    query = db.query(WorkspaceMember).filter(
        WorkspaceMember.user_id == user.id, WorkspaceMember.status == "active"
    )
    # A token is scoped to one tenant and must not enumerate the others
    # its owner belongs to. This route takes only CurrentUser, so the
    # pinning in `get_current_workspace` never runs — narrow explicitly
    # (ADR-0029).
    pinned = api_token_workspace_id(request)
    if pinned is not None:
        query = query.filter(WorkspaceMember.workspace_id == pinned)
    memberships = query.all()
    out = []
    for m in memberships:
        ws = db.get(Workspace, m.workspace_id)
        if ws:
            out.append(
                {
                    "id": str(ws.id),
                    "name": ws.name,
                    "kind": ws.kind,
                    "currency_default": ws.currency_default,
                }
            )
    return ok(out)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(forbid_api_token)],
)
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
    ws = Workspace(
        name=payload.name,
        kind="organization",
        owner_user_id=user.id,
        currency_default=payload.currency_default,
    )
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


def _serialize_workspace(
    ws: Workspace,
    new_token: str | None = None,
    provider_credentials: list[dict] | None = None,
) -> dict:
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
        "sourcing_language_code": ws.sourcing_language_code,
        "sourcing_preferred_distributors": ws.sourcing_preferred_distributors,
        "active_currencies": ws.active_currencies,
        "active_countries": ws.active_countries,
        "active_distributors": ws.active_distributors,
        "sourcing_use_cached_for_dashboards": bool(
            ws.sourcing_use_cached_for_dashboards
        ),
        # Sourcing credentials follow the encrypted-at-rest parts-provider pattern.
        "has_sourcing_company_id": bool(ws.sourcing_company_id_enc),
        "has_sourcing_api_key": bool(ws.sourcing_api_key_enc),
        "scanner": ws.scanner or "zxing",
        # Same secret-handling pattern as the parts-provider key.
        "has_scanner_license_key": bool(ws.scanner_license_key),
        # Secondary-provider credentials — presence flags only, same rule
        # as every has_* above. One entry per configured provider; the
        # primary appears here too once it has a row (migration 0070
        # backfilled one for every workspace that had a key).
        "provider_credentials": provider_credentials or [],
    }
    if new_token is not None:
        # Shown once — the frontend must present a copy-once UI.
        out["catalog_token_plaintext"] = new_token
    return out


def _provider_credentials_payload(db, ws: Workspace) -> list[dict]:
    return [serialize_credential(row) for row in active_credential_rows(db, ws.id)]


@router.get("/current")
def current(db: DbSession, ws: CurrentWorkspace):
    return ok(
        _serialize_workspace(
            ws, provider_credentials=_provider_credentials_payload(db, ws)
        )
    )


@router.get("/master-lists")
def master_lists():
    return ok(
        {
            "currencies": ALL_CURRENCIES,
            "countries": ALL_COUNTRIES,
            "distributors": ALL_DISTRIBUTORS,
        }
    )


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


# Session-cookie only (ADR-0029): this route writes the workspace's
# encrypted provider credentials, so a leaked admin token could rotate
# them — durable control that survives revoking the token. The ordinary
# settings it also carries are not worth that, and an agent that needs
# them can get a narrower endpoint later.
@router.patch(
    "/current",
    dependencies=[Depends(require_role("admin")), Depends(forbid_api_token)],
)
def patch_current(payload: WorkspacePatch, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    data = payload.model_dump(exclude_unset=True)
    regenerate = bool(data.pop("regenerate_catalog_token", False))
    was_enabled = bool(ws.catalog_enabled)
    previous_sourcing_provider = ws.sourcing_provider or "none"
    _validate_sourcing_defaults_against_active_lists(data, ws)
    active_list_changes = {
        key: value
        for key, value in data.items()
        if key in {"active_currencies", "active_countries", "active_distributors"}
    }

    # Track which credential fields were changed so we can emit a
    # single audit row.  NEVER store the plaintext values in the audit
    # log — only the field names.
    _credential_fields_changed: list[str] = []

    # Credential fields need special handling so empty input clears
    # rather than storing an empty string. They are encrypted at rest via
    # app.core.secrets (Sec HIGH-9).
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
    if "sourcing_company_id" in data:
        new_company_id = data.pop("sourcing_company_id")
        ws.sourcing_company_id_enc = _encrypt_patch_credential(new_company_id)
        _credential_fields_changed.append("sourcing_company_id")
    if "sourcing_api_key" in data:
        new_api_key = data.pop("sourcing_api_key")
        ws.sourcing_api_key_enc = _encrypt_patch_credential(new_api_key)
        _credential_fields_changed.append("sourcing_api_key")

    for k, v in data.items():
        setattr(ws, k, v)

    sourcing_provider_changed = (
        "sourcing_provider" in payload.model_fields_set
        and (ws.sourcing_provider or "none") != previous_sourcing_provider
    )
    sourcing_credentials_changed = any(
        field in _credential_fields_changed
        for field in ("sourcing_company_id", "sourcing_api_key")
    )
    if (
        sourcing_credentials_changed
        or (
            sourcing_provider_changed
            and "trustedparts" in {previous_sourcing_provider, ws.sourcing_provider}
        )
    ):
        sourcing_cache.purge_provider_cache(
            db,
            workspace_id=ws.id,
            provider="trustedparts",
        )
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

    if active_list_changes:
        _audit_log(
            db,
            ws=ws,
            user=user,
            action="workspace.active_lists_updated",
            target_type="workspace",
            target_ids=[ws.id],
            comment=json.dumps(
                {
                    "fields": sorted(active_list_changes),
                    "values": active_list_changes,
                },
                separators=(",", ":"),
            ),
        )

    return ok(
        _serialize_workspace(
            ws,
            new_token=new_token,
            provider_credentials=_provider_credentials_payload(db, ws),
        )
    )


# Session-cookie only, same reasoning as PATCH /current: this writes an
# encrypted third-party credential, so a leaked admin token must not be
# able to swap the workspace's provider key for one it controls
# (ADR-0029).
@router.put(
    "/current/provider-credentials",
    dependencies=[Depends(require_role("admin")), Depends(forbid_api_token)],
)
@limiter.limit("30/minute", key_func=workspace_key)
def put_provider_credentials(
    request: Request,
    payload: ProviderCredentialsIn,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    """Set or clear one SECONDARY provider's credentials (admin+).

    `workspace_provider_credentials` holds secondaries and nothing else.
    The primary's key lives in the legacy `workspaces.parts_provider_api_*`
    columns and is written by `PATCH /current`; letting it also land here
    would give one provider two credential stores, and clearing either
    one would report success while the other kept authenticating. So a
    payload naming the workspace's own `parts_provider` is refused.

    Omitted fields are left alone, an empty string clears one, and
    clearing both retires the row.

    Never echoes a credential: the response carries presence flags, and
    the audit row records the provider plus the field NAMES only.
    """
    if payload.provider == (ws.parts_provider or None):
        raise_http(
            400,
            ErrorCodes.WORKSPACE_PROVIDER_IS_PRIMARY,
            (
                f"'{payload.provider}' is this workspace's primary provider; "
                "rotate its credentials with PATCH /api/workspaces/current"
            ),
            provider=payload.provider,
        )

    row = _upsert_provider_credentials(
        db,
        ws=ws,
        user_id=user.id,
        provider=payload.provider,
        api_key=payload.api_key,
        api_secret=payload.api_secret,
    )

    changed = sorted(payload.model_fields_set - {"provider"})
    _audit_log(
        db,
        ws=ws,
        user=user,
        action="workspace.credentials_rotated",
        target_type="workspace",
        target_ids=[ws.id],
        comment=f"provider={payload.provider},fields={','.join(changed)}",
        request_id=getattr(request.state, "request_id", None),
    )
    return ok(
        {
            "provider": payload.provider,
            "has_api_key": bool(row is not None and row.api_key_encrypted),
            "has_api_secret": bool(row is not None and row.api_secret_encrypted),
            "provider_credentials": _provider_credentials_payload(db, ws),
        }
    )


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


# Credential and membership administration is session-cookie only: an API
# token must not be able to mint another credential (a catalog token
# outlives revoking the PAT), change anyone's role, or remove a seat.
# ADR-0029 / `core/deps.py::forbid_api_token`.
@router.post(
    "/current/catalog/tokens",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin")), Depends(forbid_api_token)],
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
    dependencies=[Depends(require_role("admin")), Depends(forbid_api_token)],
)
def revoke_catalog_token(token_id: UUID, db: DbSession, ws: CurrentWorkspace):
    """Revoke a catalog token (admin+).

    Sets revoked_at = now(). Cross-workspace access → 404 (never 403).
    """
    try:
        t = assert_in_workspace(db, WorkspaceCatalogToken, token_id, ws.id, label="catalog token")
    except HTTPException:
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


@router.patch(
    "/members/{member_id}",
    dependencies=[Depends(require_role("admin")), Depends(forbid_api_token)],
)
def patch_member(
    member_id: UUID,
    payload: MemberPatch,
    db: DbSession,
    ws: CurrentWorkspace,
    user: CurrentUser,
):
    try:
        m = assert_in_workspace(db, WorkspaceMember, member_id, ws.id, label="member")
    except HTTPException:
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


@router.delete(
    "/members/{member_id}",
    dependencies=[Depends(require_role("admin")), Depends(forbid_api_token)],
)
def remove_member(member_id: UUID, db: DbSession, ws: CurrentWorkspace, user: CurrentUser):
    try:
        m = assert_in_workspace(db, WorkspaceMember, member_id, ws.id, label="member")
    except HTTPException:
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
    # Revoke the departing member's API tokens BEFORE the membership row
    # goes away. Authentication re-checks membership, so those tokens
    # already 401 the moment the seat is gone — but an un-revoked token
    # would silently come back to life if the person is later re-invited,
    # possibly at a lower role than the token was minted under. The seat
    # and its credentials share one lifecycle (ADR-0029).
    tokens_service.revoke_all_for_user(db, workspace_id=ws.id, user_id=m.user_id)
    db.delete(m)
    return ok(None, "removed")


@router.post("/{workspace_id}/switch", dependencies=[Depends(forbid_api_token)])
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
        key=WORKSPACE_COOKIE_NAME,
        value=str(workspace_id),
        max_age=365 * 24 * 3600,
        **workspace_cookie_attrs(),
    )
    return ok({"workspace_id": str(workspace_id)})
