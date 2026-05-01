"""Symmetric encryption for workspace-level secrets at rest.

The 2026-04-30 review's Sec HIGH-9: workspace.parts_provider_api_key /
parts_provider_api_secret / scanner_license_key were stored as
plaintext columns. A DB dump (legitimate backup, replica, log) leaked
every workspace's third-party credentials.

Uses Fernet (AES-128-CBC + HMAC-SHA256, with timestamp + version) keyed
off `WORKSPACE_SECRETS_KEY` (a base64-encoded 32-byte Fernet key from
env). Generate one with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Storage format: the same base64 token Fernet emits, written into a
String column. `decrypt()` returns the plaintext or `None` if the
input is empty / None — letting routes ergonomically pass the column
through without nullable-handling boilerplate.

Falls back to a dev-only default key when `WORKSPACE_SECRETS_KEY` is
empty (zero-config local runs + low-stakes throwaway environments).
The fallback emits a one-shot warning to the structured-logging
foundation so the operator sees it on container start; production
operators must set the env var explicitly.

Operational hazard: losing `WORKSPACE_SECRETS_KEY` makes every encrypted
column unrecoverable. Escrow alongside SESSION_SECRET.
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.logging import get_logger


log = get_logger(__name__)


# Dev-only default — weak by design so local runs work without a
# `.env` step. Prod overrides via the `WORKSPACE_SECRETS_KEY` env var
# (set in `.env.prod` and not committed). If you ever change THIS
# default in dev, all dev-encrypted secrets become garbage; fine
# locally, but a discovery-time gotcha for someone who skipped reading
# this comment.
_DEV_DEFAULT_KEY = b"OXmO1Y_-zTtTJ_NXxL5RQqGsbwI3wQAOJ-V_M5HH4_o="


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    from app.core.config import settings

    key = settings().WORKSPACE_SECRETS_KEY
    if not key:
        # Warn once. lru_cache means this fires on the first credential
        # write/read after process start, then never again until the
        # cache is cleared (test fixtures or a process restart).
        log.warning(
            "WORKSPACE_SECRETS_KEY not set; encrypting workspace credentials "
            "under the dev fallback key. Generate a real key for prod with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
        return Fernet(_DEV_DEFAULT_KEY)
    return Fernet(key.encode("ascii"))


def encrypt(plaintext: str | None) -> str | None:
    """Encrypt a plaintext string for storage. None → None (lets
    callers pass nullable columns straight through)."""
    if plaintext is None:
        return None
    if plaintext == "":
        # Treat empty-string the same as None — the route layer uses
        # empty payloads as "clear this credential".
        return None
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str | None) -> str | None:
    """Decrypt a stored token back to plaintext. None / empty → None.
    A token encrypted with a different key (e.g. after rotation
    without re-encryption) raises `cryptography.fernet.InvalidToken` —
    callers should let that propagate so the API errors loudly rather
    than silently treating the credential as unset."""
    if not token:
        return None
    return _fernet().decrypt(token.encode("ascii")).decode("utf-8")


def safe_decrypt(token: str | None) -> str | None:
    """Lenient decrypt — used during the migration backfill where rows
    that were not yet encrypted (any pre-migration row) need to flow
    through unchanged. Outside the migration, prefer `decrypt()` so a
    key rotation surfaces immediately rather than corrupting the
    request silently."""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        return token
