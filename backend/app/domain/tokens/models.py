from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID

from app.domain._mixins import WorkspaceOwned
from app.infra.db import Base


class ApiToken(WorkspaceOwned, Base):
    """A personal access token: non-cookie auth for KiCad and agents.

    The token acts AS `user_id` inside `workspace_id` — it carries no
    privileges of its own. The membership row still decides what the
    request may do, so a viewer's token cannot write even when
    `read_only` is false.

    `token_hmac` is HMAC-SHA256(secret, SESSION_SECRET); the plaintext
    is returned exactly once at mint and never stored. See
    `service.py` for the composite `smk_{id}.{secret}` format and
    `docs/adr/0029-api-tokens-and-csrf-exemption.md` for why the id
    travels in the plaintext.
    """

    __tablename__ = "api_tokens"
    __table_args__ = (
        # The listing paths filter on (workspace_id, revoked_at); auth
        # itself is a PK lookup and needs no index of its own.
        Index("ix_api_tokens_ws_revoked", "workspace_id", "revoked_at"),
    )

    # CASCADE: a deleted user's tokens must not outlive them. There is no
    # SET NULL option here — a token with no owner has no role to resolve.
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label = Column(String(120), nullable=False)
    # Hex digest — String(64) is exactly SHA-256's hex width.
    token_hmac = Column(String(64), nullable=False)
    # Read-only tokens refuse every non-GET/HEAD/OPTIONS request. They are
    # what the KiCad HTTP library and the PCM repository (phases 5/6) hand
    # out, since those protocols put the token somewhere leaky (a config
    # file, a URL path).
    read_only = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    # Best-effort telemetry, written outside the auth decision — a failure
    # here never fails authentication.
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    last_used_ip = Column(String(64), nullable=True)
