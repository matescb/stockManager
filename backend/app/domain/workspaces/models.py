from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.time import utcnow
from app.domain.workspaces.master_lists import (
    DEFAULT_ACTIVE_COUNTRIES,
    DEFAULT_ACTIVE_CURRENCIES,
    DEFAULT_ACTIVE_DISTRIBUTORS,
)
from app.infra.db import Base


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint(
            "sourcing_language_code IS NULL OR sourcing_language_code IN "
            "('de','en','es','fr','it','pt','ja','zh-hans','zh-hant')",
            name="ck_workspaces_sourcing_language_code",
        ),
        # Partial unique index: only non-NULL hashes must be distinct.
        # Mirrors the index created by migration 0025.
        Index(
            "ix_workspaces_catalog_token_hash",
            "catalog_token_hash",
            unique=True,
            postgresql_where=sa.text("catalog_token_hash IS NOT NULL"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    kind = Column(String(20), nullable=False, default="organization")  # personal | organization
    owner_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    currency_default = Column(String(3), nullable=False, default="USD")
    lot_control_enabled = Column(Boolean, nullable=False, default=True)
    serial_tracking_enabled = Column(Boolean, nullable=False, default=False)
    catalog_token = Column(String(64), nullable=True)
    # HMAC-SHA256 of the plaintext catalog_token, keyed by SESSION_SECRET.
    # The application looks up workspaces by this hash — never by the
    # plaintext — so the token never appears in a WHERE clause.
    # Nullable for rows that pre-date migration 0025 and have not yet had
    # their token rotated.  See SEC2-008 / issue #71.
    catalog_token_hash = Column(String(64), nullable=True)
    catalog_enabled = Column(Boolean, nullable=False, default=False)
    parts_provider = Column(String(40), nullable=False, default="none")  # none | mouser | digikey
    # Encrypted at rest via app.core.secrets (Sec HIGH-9). Fernet
    # ciphertext is ~30% larger than plaintext after base64; column
    # widened by 0016 to leave headroom for typical 36–48 char keys.
    parts_provider_api_key = Column(String(1024), nullable=True)
    # DigiKey needs a second credential (client_secret). Mouser leaves this NULL.
    parts_provider_api_secret = Column(String(1024), nullable=True)
    sourcing_provider = Column(
        String(40),
        nullable=False,
        default="none",
        server_default="none",
    )
    sourcing_company_id_enc = Column(String(1024), nullable=True)
    sourcing_api_key_enc = Column(String(1024), nullable=True)
    sourcing_country_code = Column(String(2), nullable=True)
    sourcing_currency_code = Column(String(3), nullable=True)
    sourcing_language_code = Column(String(10), nullable=True)
    sourcing_preferred_distributors = Column(JSONB, nullable=True)
    active_currencies = Column(
        JSONB,
        nullable=False,
        default=lambda: list(DEFAULT_ACTIVE_CURRENCIES),
        server_default=sa.text('\'["EUR","USD","CZK","GBP"]\'::jsonb'),
    )
    active_countries = Column(
        JSONB,
        nullable=False,
        default=lambda: list(DEFAULT_ACTIVE_COUNTRIES),
        server_default=sa.text('\'["CZ","DE","US","GB"]\'::jsonb'),
    )
    active_distributors = Column(
        JSONB,
        nullable=False,
        default=lambda: list(DEFAULT_ACTIVE_DISTRIBUTORS),
        server_default=sa.text('\'["DigiKey","Mouser","Farnell","TME","LCSC"]\'::jsonb'),
    )
    sourcing_use_cached_for_dashboards = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=sa.true(),
    )
    # Which client-side decoder the scanner pages mount. 'zxing' is the
    # royalty-free default; 'scandit' is opt-in and consumes scanner_license_key.
    scanner = Column(String(40), nullable=False, default="zxing")  # zxing | scandit
    scanner_license_key = Column(String(4096), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(40), nullable=False, default="member")  # owner | admin | member | viewer
    status = Column(String(20), nullable=False, default="active")  # invited | active | disabled
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class WorkspaceInvitation(Base):
    __tablename__ = "workspace_invitations"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_workspace_invitation_token_hash"),
        # Partial unique index: at most one pending invitation per
        # (workspace, email) pair. Added by migration 0023 (BE2-020 / #65).
        # The partial condition (status = 'pending') allows a new invite
        # after the previous one is accepted or revoked.
        Index(
            "uq_workspace_invitation_pending",
            "workspace_id",
            "email",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email = Column(String(320), nullable=False, index=True)
    role = Column(String(40), nullable=False, default="member")
    # SHA-256 hex digest of the plaintext token. Kept for backward
    # compatibility; new rows also set token_hmac. See token_hmac below.
    token_hash = Column(String(64), nullable=False)
    # HMAC-SHA-256 (keyed on SESSION_SECRET) of the plaintext token.
    # SEC2-013: the accept flow looks up by id (PK) and then calls
    # hmac.compare_digest against this column — no timing oracle.
    # Migration 0021 adds this column; rows created before 0021 have
    # token_hmac=NULL and cannot be accepted (pending invitations
    # are invalidated on deploy — see migration docstring).
    token_hmac = Column(String(64), nullable=True)
    status = Column(String(20), nullable=False, default="pending")  # pending | accepted | revoked
    invited_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    accepted_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class WorkspaceCatalogToken(Base):
    """Per-recipient catalog access token (SEC2-019 / issue #77).

    Replaces the single-token-per-workspace model with a child table
    so individual tokens can be labelled (per recipient) and revoked
    without affecting other consumers.

    token_hmac is HMAC-SHA256(plaintext, SESSION_SECRET) — never the
    plaintext itself.  The plaintext is returned exactly once at creation
    time in the API response and is never stored.
    """
    __tablename__ = "workspace_catalog_tokens"
    __table_args__ = (
        # Partial unique: same HMAC can only appear once per workspace
        # among un-revoked tokens (two workspaces can coincidentally share
        # an HMAC; revoked tokens are excluded so a re-issued token for the
        # same underlying string doesn't collide with its revoked predecessor).
        Index(
            "uq_catalog_tokens_ws_hmac_active",
            "workspace_id",
            "token_hmac",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # HMAC-SHA256 of plaintext keyed by SESSION_SECRET. String(64) = hex digest.
    token_hmac = Column(String(64), nullable=False)
    label = Column(String(120), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    created_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    last_used_ip = Column(String(45), nullable=True)
