from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.infra.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Workspace(Base):
    __tablename__ = "workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    kind = Column(String(20), nullable=False, default="organization")  # personal | organization
    owner_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    currency_default = Column(String(3), nullable=False, default="USD")
    lot_control_enabled = Column(Boolean, nullable=False, default=True)
    serial_tracking_enabled = Column(Boolean, nullable=False, default=False)
    catalog_token = Column(String(64), nullable=True)
    catalog_enabled = Column(Boolean, nullable=False, default=False)
    parts_provider = Column(String(40), nullable=False, default="none")  # none | mouser | digikey
    # Encrypted at rest via app.core.secrets (Sec HIGH-9). Fernet
    # ciphertext is ~30% larger than plaintext after base64; column
    # widened by 0016 to leave headroom for typical 36–48 char keys.
    parts_provider_api_key = Column(String(1024), nullable=True)
    # DigiKey needs a second credential (client_secret). Mouser leaves this NULL.
    parts_provider_api_secret = Column(String(1024), nullable=True)
    # Which client-side decoder the scanner pages mount. 'zxing' is the
    # royalty-free default; 'scandit' is opt-in and consumes scanner_license_key.
    scanner = Column(String(40), nullable=False, default="zxing")  # zxing | scandit
    scanner_license_key = Column(String(4096), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(40), nullable=False, default="member")  # owner | admin | member | viewer
    status = Column(String(20), nullable=False, default="active")  # invited | active | disabled
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)


class WorkspaceInvitation(Base):
    __tablename__ = "workspace_invitations"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_workspace_invitation_token_hash"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(String(320), nullable=False, index=True)
    role = Column(String(40), nullable=False, default="member")
    # SHA-256 hex digest of the plaintext token. The plaintext is
    # returned to the caller exactly once at creation time and is never
    # persisted; without this, a DB dump leaks every pending invitation
    # as a replayable credential.
    token_hash = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, default="pending")  # pending | accepted | revoked
    invited_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    accepted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
