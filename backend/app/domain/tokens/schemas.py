"""Pydantic shapes for `/api/tokens`.

`ApiTokenOut` deliberately has no `token_hmac` field and no `token`
field: the plaintext exists only in `ApiTokenCreated`, returned once
by `POST /api/tokens`. Every other read of a token row goes through
`ApiTokenOut`, so there is no serialisation path that can leak either
the secret or its digest.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ApiTokenIn", "ApiTokenOut", "ApiTokenCreated"]

# One year. Long enough for a KiCad workstation nobody wants to re-pair
# every quarter, short enough that "no expiry" stays a deliberate choice.
MAX_EXPIRY_DAYS = 365


class ApiTokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    read_only: bool = False
    # None means "never expires" — the common case for an agent that has
    # to keep working unattended. Bounded on both ends so an out-of-range
    # int can't reach the timedelta arithmetic.
    expires_in_days: int | None = Field(default=None, ge=1, le=MAX_EXPIRY_DAYS)


class ApiTokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    label: str
    read_only: bool
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    # Only populated by the admin-only `?all=true` listing, so an admin
    # can tell whose token they're about to revoke. Never set on the
    # own-tokens path — the caller already knows who they are.
    user_email: str | None = None


class ApiTokenCreated(ApiTokenOut):
    """Mint response. The ONLY shape in the codebase carrying a plaintext
    token; `token` is never re-derivable from the stored row."""

    token: str
