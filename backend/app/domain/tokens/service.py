"""Personal-access-token minting, resolution and revocation.

Plaintext format
----------------
``smk_{token_id.hex}.{secrets.token_urlsafe(32)}``

* ``smk_`` — a fixed, greppable prefix. Secret scanners and
  ``git grep`` find a leaked token without knowing its shape, and it
  gives the parser a cheap reject for "this isn't one of ours".
* ``{token_id.hex}`` — the row's primary key. Resolution is therefore a
  PK equality lookup followed by a constant-time HMAC comparison, which
  is the same trick `api/routes/invitations.py` uses for the composite
  ``{id}:{secret}`` invitation token (SEC2-013): a scan over a hashed
  column is itself a timing oracle, a PK lookup is not.
* ``{secret}`` — 256 bits from ``secrets.token_urlsafe``. Only its
  ``SESSION_SECRET``-keyed HMAC is stored, so a database dump cannot be
  replayed against the API.

Every failure mode — malformed, unknown id, wrong secret, revoked,
expired — collapses to ``resolve() is None``, so the caller has exactly
one error to raise and there is no oracle distinguishing "no such
token" from "wrong secret".

Writes ``db.flush()``; the ``get_db`` dependency owns the commit.
"""
from __future__ import annotations

import hmac as _hmac
import secrets
import uuid
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utcnow
from app.domain.tokens.models import ApiToken
from app.domain.tokens.schemas import ApiTokenIn
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace

__all__ = [
    "TOKEN_PREFIX",
    "hmac_secret",
    "mint_token",
    "parse_plaintext",
    "resolve_token",
    "record_use",
    "TELEMETRY_MIN_INTERVAL_SECONDS",
    "list_own",
    "list_workspace",
    "get_in_workspace",
    "revoke",
    "revoke_all_for_user",
]

TOKEN_PREFIX = "smk_"

# 32 bytes → 43 urlsafe-base64 chars. Matches the entropy of the session
# and invitation tokens already in use.
_SECRET_BYTES = 32

# Minimum gap between `last_used_at` writes for one token. See `record_use`.
TELEMETRY_MIN_INTERVAL_SECONDS = 300


def hmac_secret(secret: str) -> str:
    """HMAC-SHA-256 (keyed on SESSION_SECRET) hex digest of the secret half.

    Same construction as `core/auth.py::hmac_token` and the catalog
    token's `_hmac_token`. Kept local rather than imported so this
    module's storage format is legible in one place.
    """
    key = settings().SESSION_SECRET.encode("utf-8")
    return _hmac.new(key, secret.encode("utf-8"), "sha256").hexdigest()


def mint_token(
    db: Session,
    *,
    ws: Workspace,
    user: User,
    payload: ApiTokenIn,
) -> tuple[ApiToken, str]:
    """Create a token row and return it with its one-and-only plaintext.

    The row id is generated here rather than left to the column default
    because the id is half the plaintext — we need it before the INSERT.
    """
    token_id = uuid.uuid4()
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    expires_at = (
        utcnow() + timedelta(days=payload.expires_in_days)
        if payload.expires_in_days is not None
        else None
    )
    row = ApiToken(
        id=token_id,
        workspace_id=ws.id,
        user_id=user.id,
        label=payload.label,
        token_hmac=hmac_secret(secret),
        read_only=payload.read_only,
        expires_at=expires_at,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(row)
    db.flush()
    return row, f"{TOKEN_PREFIX}{token_id.hex}.{secret}"


def parse_plaintext(raw: str) -> tuple[UUID, str] | None:
    """Split ``smk_{id}.{secret}`` into its halves, or None if malformed.

    Returns None — never raises — so the caller has a single failure
    path for every kind of bad input.
    """
    if not raw.startswith(TOKEN_PREFIX):
        return None
    id_hex, sep, secret = raw[len(TOKEN_PREFIX):].partition(".")
    if not sep or not secret:
        return None
    try:
        return UUID(id_hex), secret
    except ValueError:
        return None


def resolve_token(db: Session, raw: str) -> ApiToken | None:
    """Return the live token row for a plaintext, or None.

    None covers malformed input, unknown id, wrong secret, revoked and
    expired alike. The HMAC of the supplied secret is computed before
    branching on whether the row exists, so a missing row and a wrong
    secret do the same amount of crypto work (mirrors
    `invitations.py::accept_invitation`).
    """
    parsed = parse_plaintext(raw)
    if parsed is None:
        return None
    token_id, secret = parsed

    row = db.get(ApiToken, token_id)
    supplied = hmac_secret(secret)
    if row is None or not _hmac.compare_digest(supplied, row.token_hmac or ""):
        return None
    if row.revoked_at is not None:
        return None
    if row.expires_at is not None and row.expires_at <= utcnow():
        return None
    # Nothing sets `archived_at` on a token today — it comes from the
    # WorkspaceOwned mixin. Checked anyway so that if an archive endpoint
    # ever lands, it can't accidentally leave archived tokens live.
    if row.archived_at is not None:
        return None
    return row


def record_use(db: Session, row: ApiToken, *, client_ip: str | None) -> bool:
    """Stamp last-used telemetry. Returns True when a write happened.

    Callers MUST treat this as best-effort — see `core/deps.py`, which
    logs and swallows any failure rather than turning a telemetry
    problem into a 500 on an otherwise valid request.

    Throttled to one UPDATE per `TELEMETRY_MIN_INTERVAL_SECONDS`: KiCad's
    part chooser polls on a 60s parts / 600s categories cadence and an
    agent loop can be far busier, so an unthrottled write would make
    every read a read-write transaction contending on one row. The cost
    is precision — `last_used_at` is accurate to within the interval,
    which is all "is this token still in use?" needs.
    """
    now = utcnow()
    if (
        row.last_used_at is not None
        and (now - row.last_used_at).total_seconds() < TELEMETRY_MIN_INTERVAL_SECONDS
    ):
        return False
    row.last_used_at = now
    if client_ip:
        # Column is String(64) — an IPv6 address with a zone id fits, but
        # a spoofed X-Forwarded-For chain would not. get_remote_address
        # gives us the socket peer, so this is bounded in practice; the
        # slice is belt-and-braces against a future proxy-header change.
        row.last_used_ip = client_ip[:64]
    db.flush()
    return True


def list_own(db: Session, *, ws: Workspace, user: User) -> list[ApiToken]:
    """The caller's own tokens in this workspace, newest first."""
    return list(
        db.execute(
            select(ApiToken)
            .where(ApiToken.workspace_id == ws.id)
            .where(ApiToken.user_id == user.id)
            .order_by(ApiToken.created_at.desc())
        ).scalars()
    )


def list_workspace(db: Session, *, ws: Workspace) -> list[tuple[ApiToken, str | None]]:
    """Every token in the workspace paired with its owner's email.

    Admin-only (enforced by the route) — it exists so an admin can find
    and revoke a departed teammate's tokens.
    """
    rows = db.execute(
        select(ApiToken, User.email)
        .outerjoin(User, User.id == ApiToken.user_id)
        .where(ApiToken.workspace_id == ws.id)
        .order_by(ApiToken.created_at.desc())
    ).all()
    return [(row[0], row[1]) for row in rows]


def get_in_workspace(db: Session, *, ws: Workspace, token_id: UUID) -> ApiToken | None:
    """PK lookup constrained to the workspace. Returns None for both
    "no such token" and "someone else's workspace" — the caller turns
    that into a 404, never a 403, so a foreign id can't be probed
    (ADR-0002)."""
    return db.execute(
        select(ApiToken)
        .where(ApiToken.id == token_id)
        .where(ApiToken.workspace_id == ws.id)
    ).scalar_one_or_none()


def revoke_all_for_user(db: Session, *, workspace_id: UUID, user_id: UUID) -> int:
    """Revoke every live token a user holds in one workspace. Returns the count.

    Called when a member is removed from a workspace. Authentication
    re-checks membership on every request, so a departed member's tokens
    already fail with a 401 — but leaving them un-revoked means a
    re-invite (possibly at a *lower* role) silently reanimates every
    credential they held before. Revoking at removal makes the seat and
    its tokens one lifecycle.
    """
    return int(
        db.query(ApiToken)
        .filter(
            ApiToken.workspace_id == workspace_id,
            ApiToken.user_id == user_id,
            ApiToken.revoked_at.is_(None),
        )
        .update({ApiToken.revoked_at: utcnow()}, synchronize_session=False)
    )


def revoke(db: Session, *, row: ApiToken, user: User) -> ApiToken:
    """Soft-revoke. Idempotent: an already-revoked row keeps its original
    `revoked_at` so the audit trail records when it actually died."""
    if row.revoked_at is None:
        row.revoked_at = utcnow()
        row.updated_by = user.id
        db.flush()
    return row
