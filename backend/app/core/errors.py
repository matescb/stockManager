"""Standardised error-raising helper for the API layer.

Every error response in this app flows through
`app.core.responses.http_exception_handler`, which spreads the
`HTTPException(detail=...)` dict onto the top-level response so the
frontend can read structured fields (e.g. `existing_id` on a 409). The
historical mix of `detail="string"` and `detail={dict}` callsites makes
the response shape inconsistent — code that expects `body.existing_id`
breaks against routes that returned a bare string.

`raise_http(status, code, message=None, **fields)` always raises
`HTTPException(detail={"code", "message", **fields})`, giving the
frontend a stable machine-readable `code` to switch on, and a
human-readable `message` orthogonal to the response category derived
from the status code.

This module is being adopted incrementally — see issue #125 and the
plan attached to it. PR1 covers auth + workspaces + invitations +
sentry_tunnel; PR2 the domain services; PR3 the CRUD routes.

Canonical status codes used by this app
---------------------------------------
- 401 — unauthenticated (no/expired session)
- 403 — forbidden (authenticated but lacks the role for this resource;
        only after resource existence has been confirmed — see
        `app.api._helpers.require_resource_access`)
- 404 — not-found OR cross-workspace. NEVER 403 for cross-workspace —
        that would leak existence to a foreign-id probe
        (workspace-isolation invariant).
- 409 — conflict (MPN dup, over-receive, version conflict, …)
- 422 — validation. Pydantic auto-generates these via
        `validation_exception_handler`; a manual 422 is rare.
- 400 — malformed-request shape that Pydantic didn't catch (e.g. a
        free-form string field that fails a domain-specific rule).
- 5xx — never raised manually.
"""
from __future__ import annotations

from typing import Any, NoReturn

from fastapi import HTTPException

# Default human-readable messages keyed by status code. Used when the
# caller passes `message=None` so a route only has to pick a `code`.
_DEFAULT_MESSAGES: dict[int, str] = {
    400: "bad request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not found",
    409: "conflict",
    413: "payload too large",
    422: "validation failed",
}


def raise_http(
    status_code: int,
    code: str,
    message: str | None = None,
    **fields: Any,
) -> NoReturn:
    """Raise an `HTTPException` with a structured dict-form `detail`.

    The `detail` is always a dict containing at minimum `code` and
    `message`. Any additional keyword arguments are spread into the
    detail dict and surface on the top-level response (alongside the
    `data: null` and `status: {category, message}` envelope), so the
    frontend can read e.g. `body.existing_id` directly.

    `code` is a stable machine-readable string the frontend can switch
    on (`"workspace.not_found"`, `"part.mpn_conflict"`, …). It is
    orthogonal to the response category, which is derived from
    `status_code` by `responses._category_for_status`.
    """
    detail: dict[str, Any] = {
        "code": code,
        "message": message or _DEFAULT_MESSAGES.get(status_code, ""),
    }
    for k, v in fields.items():
        if k in detail:
            # Refuse to silently overwrite the canonical keys.
            raise ValueError(f"reserved field name: {k}")
        detail[k] = v
    raise HTTPException(status_code=status_code, detail=detail)


class ErrorCodes:
    """Stable string constants for the `code` field.

    Frontend code can switch on these without depending on humanly-
    written `message` strings. Group-prefixed (`<area>.<reason>`) so the
    namespace stays organised as more codes are added.

    Add new codes here as you migrate callsites. Do not rename existing
    ones — once shipped, they're part of the FE contract.
    """

    # Auth / session
    AUTH_INVALID_CREDENTIALS = "auth.invalid_credentials"
    AUTH_EMAIL_TAKEN = "auth.email_taken"
    AUTH_WEAK_PASSWORD = "auth.weak_password"
    AUTH_ACCOUNT_LOCKED = "auth.account_locked"
    AUTH_VERIFICATION_PENDING = "auth.verification_pending"
    AUTH_VERIFICATION_INVALID = "auth.verification_invalid"
    AUTH_VERIFICATION_EXPIRED = "auth.verification_expired"

    # Workspace / membership
    WORKSPACE_NOT_FOUND = "workspace.not_found"
    WORKSPACE_OWNER_CAP = "workspace.owned_cap_reached"
    WORKSPACE_MEMBER_NOT_FOUND = "workspace.member_not_found"
    WORKSPACE_OWNER_ONLY = "workspace.owner_only"
    WORKSPACE_LAST_OWNER = "workspace.last_owner"
    WORKSPACE_SELF_REMOVE = "workspace.self_remove"

    # Invitations
    INVITATION_NOT_FOUND = "invitation.not_found"
    INVITATION_ALREADY_MEMBER = "invitation.already_member"
    INVITATION_NOT_PENDING = "invitation.not_pending"
    INVITATION_EMAIL_MISMATCH = "invitation.email_mismatch"

    # Sentry tunnel
    SENTRY_TUNNEL_TOO_LARGE = "sentry_tunnel.too_large"
    SENTRY_TUNNEL_EMPTY = "sentry_tunnel.empty"
    SENTRY_TUNNEL_MALFORMED_HEADER = "sentry_tunnel.malformed_header"
    SENTRY_TUNNEL_MISSING_DSN = "sentry_tunnel.missing_dsn"
    SENTRY_TUNNEL_DSN_MISMATCH = "sentry_tunnel.dsn_mismatch"

    # Auth / session — deps.py
    AUTH_NOT_AUTHENTICATED = "auth.not_authenticated"
    AUTH_INVALID_SESSION = "auth.invalid_session"
    AUTH_SESSION_EXPIRED = "auth.session_expired"
    AUTH_SESSION_IDLE_TIMEOUT = "auth.session_idle_timeout"
    AUTH_USER_MISSING = "auth.user_missing"

    # Generic resource lookup — _helpers.py / require_resource_access.
    # Prefer a domain-specific 404 code where one exists (e.g.
    # WORKSPACE_NOT_FOUND); use this on the polymorphic helpers where
    # the model is generic over workspace-owned types.
    RESOURCE_NOT_FOUND = "resource.not_found"
    RESOURCE_UNKNOWN_OBJECT_TYPE = "resource.unknown_object_type"
    RESOURCE_INSUFFICIENT_ROLE = "resource.insufficient_role"

    # Global framework errors.
    RATE_LIMITED = "rate_limited"

    # Sourcing router.
    SOURCING_WORKSPACE_NOT_CONFIGURED = "sourcing.workspace_not_configured"
    SOURCING_BUDGET_EXHAUSTED = "sourcing.budget_exhausted"
    SOURCING_PROVIDER_AUTH_FAILED = "sourcing.provider_auth_failed"
    SOURCING_PROVIDER_RATE_LIMITED = "sourcing.provider_rate_limited"
    SOURCING_PROVIDER_TIMEOUT = "sourcing.provider_timeout"
    SOURCING_PROVIDER_UNAVAILABLE = "sourcing.provider_unavailable"
    SOURCING_PLAN_EXPIRED = "sourcing.plan_expired"
    SOURCING_PART_MISSING_MPN = "sourcing.part_missing_mpn"
    SOURCING_PLAN_STALE = "sourcing.plan_stale"
    SOURCING_CURRENCY_MISMATCH = "sourcing.currency_mismatch"
    SOURCING_OVERRIDE_INVALID = "sourcing.override_invalid"
    SOURCING_TOO_MANY_DISTRIBUTORS = "sourcing.too_many_distributors"
    SOURCING_INVALID_REQUEST = "sourcing.invalid_request"
    SOURCING_GENERIC = "sourcing.error"

    # Workspace dependency-injection edge cases (deps.py).
    # WORKSPACE_NONE: caller has no active membership in any workspace
    # at all — distinct from WORKSPACE_NOT_FOUND, which is "this id
    # doesn't resolve to one of your workspaces".
    WORKSPACE_NONE = "workspace.none"

    # User-domain delete guard. Historical un-namespaced code; the FE
    # / tests already depend on this exact string, so it is preserved.
    USER_OWNS_WORKSPACES = "owns_workspaces"

    # BOM importer (domain/projects/bom_import.py).
    BOM_TOO_LARGE = "bom.too_large"
    BOM_TOO_MANY_ROWS = "bom.too_many_rows"
    # DB-005 / migration 0032 — fractional BOM quantity rejected.
    BOM_FRACTIONAL_QUANTITY = "bom.fractional_quantity"

    # Lots router.
    LOT_NOT_FOUND = "lot.not_found"
    LOT_INVALID_EXPIRATION_DATE = "lot.invalid_expiration_date"
    LOT_MOVE_STOCK_ERROR = "lot.move_stock_error"
    LOT_ADJUST_STOCK_ERROR = "lot.adjust_stock_error"

    # Storage router.
    STORAGE_NOT_FOUND = "storage.not_found"
    STORAGE_HAS_STOCK = "storage.has_stock"

    # Attachments router.
    ATTACHMENT_TOO_LARGE = "attachment.too_large"
    ATTACHMENT_EMPTY = "attachment.empty"
    ATTACHMENT_UNSUPPORTED_TYPE = "attachment.unsupported_type"
    ATTACHMENT_CONTENT_TYPE_MISMATCH = "attachment.content_type_mismatch"

    # Parts shared helpers.
    PART_NOT_FOUND = "part.not_found"

    # Projects router.
    PROJECT_NOT_FOUND = "project.not_found"

    # BOM presets router.
    BOM_PRESET_NOT_FOUND = "bom_preset.not_found"

    # Reports router.
    REPORT_PROJECT_NOT_FOUND = "report.project_not_found"

    # Catalog router.
    CATALOG_NOT_FOUND = "catalog.not_found"

    # Custom fields router.
    CUSTOM_FIELD_NOT_OVERRIDE = "custom_field.not_override"
