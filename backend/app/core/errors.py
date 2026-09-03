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
    AUTH_RESET_INVALID = "auth.reset_invalid"
    AUTH_RESET_EXPIRED = "auth.reset_expired"
    AUTH_RESET_USED = "auth.reset_used"

    # Workspace / membership
    WORKSPACE_NOT_FOUND = "workspace.not_found"
    WORKSPACE_OWNER_CAP = "workspace.owned_cap_reached"
    WORKSPACE_MEMBER_NOT_FOUND = "workspace.member_not_found"
    WORKSPACE_OWNER_ONLY = "workspace.owner_only"
    WORKSPACE_LAST_OWNER = "workspace.last_owner"
    WORKSPACE_SELF_REMOVE = "workspace.self_remove"
    WORKSPACE_ISOLATION = "workspace.isolation"
    # Refusing to write the PRIMARY provider's credentials into the
    # secondary table — that's `PATCH /api/workspaces/current`. Two
    # stores holding a key for the same provider is the two-writer trap:
    # clearing one reports success while the other keeps working.
    WORKSPACE_PROVIDER_IS_PRIMARY = "workspace.provider_is_primary"

    # Invitations
    INVITATION_NOT_FOUND = "invitation.not_found"
    INVITATION_ALREADY_MEMBER = "invitation.already_member"
    INVITATION_NOT_PENDING = "invitation.not_pending"
    INVITATION_EMAIL_MISMATCH = "invitation.email_mismatch"
    INVITATION_EXPIRED = "invitation.expired"

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

    # API tokens (PATs) — deps.py + routes/tokens.py.
    # AUTH_INVALID_TOKEN is deliberately the ONLY code for every token
    # failure (malformed, unknown, wrong secret, revoked, expired, owner
    # lost membership). Splitting it would hand an attacker an oracle
    # telling them which half of a guess was right.
    AUTH_INVALID_TOKEN = "auth.invalid_token"
    AUTH_TOKEN_READ_ONLY = "auth.token_read_only"
    AUTH_TOKEN_WORKSPACE_MISMATCH = "auth.token_workspace_mismatch"
    AUTH_TOKEN_NO_TOKEN_MANAGEMENT = "auth.token_no_token_management"

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
    SOURCING_INVALID_COUNTRY_CODE = "invalid_country_code"
    SOURCING_INVALID_CURRENCY_CODE = "invalid_currency_code"
    SOURCING_GENERIC = "sourcing.error"

    # Stock router.
    STOCK_INVALID_CURRENCY = "stock.invalid_currency"
    STOCK_BAG_SIGNATURE_MISMATCH = "stock.bag_signature_mismatch"
    STOCK_CONSTRAINT_VIOLATION = "stock.constraint_violation"
    STOCK_OPERATION_ERROR = "stock.operation_error"
    STOCK_INSUFFICIENT = "stock.insufficient"

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
    STORAGE_CONSTRAINT_VIOLATION = "storage.constraint_violation"

    # Attachments router.
    ATTACHMENT_TOO_LARGE = "attachment.too_large"
    ATTACHMENT_EMPTY = "attachment.empty"
    ATTACHMENT_UNSUPPORTED_TYPE = "attachment.unsupported_type"
    ATTACHMENT_CONTENT_TYPE_MISMATCH = "attachment.content_type_mismatch"

    # Parts shared helpers.
    PART_NOT_FOUND = "part.not_found"
    PART_HAS_RESERVED_STOCK = "part.has_reserved_stock"
    PART_MPN_CONFLICT = "part.mpn_conflict"
    PART_NAME_OR_MPN_REQUIRED = "part.name_or_mpn_required"
    PART_LINKED_PROVIDER_OWNED_FIELD = "part.linked_provider_owned_field"
    PART_NOT_META = "part.not_meta"
    PART_META_SELF_MEMBER = "part.meta_self_member"
    PART_META_MEMBER_META = "part.meta_member_meta"
    PART_PROVIDER_NOT_CONFIGURED = "part.provider_not_configured"
    PART_PROVIDER_MISSING_MPN = "part.provider_missing_mpn"
    # A provider name `make_provider` doesn't know. Distinct from
    # NOT_CONFIGURED, which means "known provider, no credentials".
    PART_PROVIDER_UNKNOWN = "part.provider_unknown"
    PART_PROVIDER_LINK_NOT_FOUND = "part.provider_link_not_found"
    # Refusing to unlink the primary through the secondary-link route —
    # that's `PATCH /api/parts/{id}` with unlink_provider=true.
    PART_PROVIDER_LINK_IS_PRIMARY = "part.provider_link_is_primary"
    PART_ASSET_NOT_FOUND = "part.asset_not_found"
    PART_ASSET_INVALID_FILENAME = "part.asset_invalid_filename"

    # Activity cursor parsing.
    ACTIVITY_INVALID_CURSOR = "activity.invalid_cursor"

    # Orders router.
    ORDER_NOT_FOUND = "order.not_found"
    ORDER_QUANTITY_ORDERED_BELOW_RECEIVED = "order.quantity_ordered_below_received"
    ORDER_DELETE_RECEIVED_ENTRY = "order.delete_received_entry"
    ORDER_RECEIVE_ERROR = "order.receive_error"

    # Builds router.
    BUILD_NOT_FOUND = "build.not_found"
    BUILD_READ_ONLY = "build.read_only"
    BUILD_CONSUME_ERROR = "build.consume_error"

    # Projects router.
    PROJECT_NOT_FOUND = "project.not_found"

    # BOM presets router.
    BOM_PRESET_NOT_FOUND = "bom_preset.not_found"

    # Categories router.
    CATEGORY_NOT_FOUND = "category.not_found"
    CATEGORY_NAME_CONFLICT = "category.name_conflict"
    CATEGORY_SLUG_CONFLICT = "category.slug_conflict"
    CATEGORY_ARCHIVED = "category.archived"
    CATEGORY_FIELD_NOT_NULLABLE = "category.field_not_nullable"

    # EDA router (symbols / footprints / 3D + SPICE data files / part_eda).
    EDA_SYMBOL_NOT_FOUND = "eda_symbol.not_found"
    EDA_FOOTPRINT_NOT_FOUND = "eda_footprint.not_found"
    EDA_DATAFILE_NOT_FOUND = "eda_datafile.not_found"
    EDA_NAME_CONFLICT = "eda.name_conflict"
    EDA_FIELD_NOT_NULLABLE = "eda.field_not_nullable"
    # Upload-lane validation (domain/eda/storage.py).
    EDA_INVALID_FILE = "eda.invalid_file"
    EDA_FILE_TOO_LARGE = "eda.file_too_large"
    EDA_UNSUPPORTED_KIND = "eda.unsupported_kind"
    EDA_EMPTY_FILE = "eda.empty_file"
    EDA_MULTIPLE_SYMBOLS = "eda.multiple_symbols"
    # Vendor-zip / LCSC import lane (domain/eda/{vendor_zip,lcsc}.py).
    EDA_INVALID_ARCHIVE = "eda.invalid_archive"
    EDA_ARCHIVE_TOO_LARGE = "eda.archive_too_large"
    EDA_LEGACY_FORMAT = "eda.legacy_format"
    EDA_MULTIPLE_FOOTPRINTS = "eda.multiple_footprints"
    EDA_NO_ENTRIES = "eda.no_entries"
    EDA_LCSC_NOT_FOUND = "eda.lcsc_not_found"
    EDA_LCSC_UNAVAILABLE = "eda.lcsc_unavailable"
    # part_eda config.
    EDA_REF_CONFLICT = "eda.ref_conflict"
    EDA_ARCHIVED = "eda.archived"
    # File serving.
    EDA_FILE_NOT_FOUND = "eda.file_not_found"
    EDA_INVALID_FILENAME = "eda.invalid_filename"

    # Reports router.
    REPORT_PROJECT_NOT_FOUND = "report.project_not_found"

    # Catalog router.
    CATALOG_NOT_FOUND = "catalog.not_found"

    # KiCad HTTP-library router. The ONLY code it raises: every failure
    # on that surface — bad token, unknown category, ineligible part —
    # is the same 404, so nothing there is an oracle.
    KICAD_NOT_FOUND = "kicad.not_found"

    # The one exception to that rule, on the PCM surface: reaching it
    # needs a valid read-only token, so it reveals nothing a caller
    # didn't already have. It means the package could not be built.
    KICAD_PACKAGE_UNAVAILABLE = "kicad.package_unavailable"

    # Custom fields router.
    CUSTOM_FIELD_RESERVED_KEY = "custom_field.reserved_key"
    CUSTOM_FIELD_NOT_OVERRIDE = "custom_field.not_override"

    # Legacy parts-provider lookup route.
    PROVIDER_UPSTREAM_ERROR = "provider.upstream_error"

    # MCP surface (ADR-0030). Raised only by the runtime assertion in
    # `app/mcp/principal.py::unit_of_work`: a tool declared read-only
    # changed the database, so its transaction was discarded. It is a
    # server bug, not a caller mistake, and the code says so — an agent
    # that sees it must not retry.
    MCP_UNDECLARED_WRITE = "mcp.undeclared_write"
