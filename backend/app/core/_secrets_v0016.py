"""Frozen snapshot of `app.core.secrets` as it existed when alembic
migration `0016_encrypt_workspace_secrets.py` was authored (2026-05-02).

DO NOT EDIT.

Why this exists (DB-010 / issue #101):
    Migration `0016` does `from app.core.secrets import encrypt,
    safe_decrypt` inside `upgrade()`. Migrations are supposed to be
    self-contained snapshots of schema-at-revision; reaching into live
    application code couples the migration's behaviour to whichever app
    revision is checked out at upgrade time. If `app.core.secrets` is
    later renamed, refactored, or has its public signatures changed,
    replaying `0016` against a fresh DB (CI clean checkout, dev reset,
    disaster recovery) breaks. CLAUDE.md's pre-edit hook explicitly
    forbids editing migrations after merge — but the migration is
    already pre-coupled to a moving target.

    This file is the frozen-shim half of the fix. It mirrors the public
    contract that `0016` depends on (`encrypt`, `safe_decrypt`,
    `_fernet`). A companion test (`tests/test_secrets_signature_pinning.py`)
    asserts `app.core.secrets.encrypt` and `safe_decrypt` keep their
    public signatures equal to this module's, so any future rename
    surfaces as a CI failure rather than a migration-time crash.

Going forward:
    All future migrations that need shared helpers must
    `from app.core._secrets_vNNNN import …` (or whatever frozen shim),
    never bare `from app.core.<module>`. Documented in
    `docs/development.md`.
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.logging import get_logger


log = get_logger(__name__)


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    from app.core.config import settings

    key = settings().WORKSPACE_SECRETS_KEY
    if not key:
        log.warning(
            "WORKSPACE_SECRETS_KEY not set; using a per-process ephemeral "
            "key. Encrypted workspace credentials will not survive a "
            "process restart. Set the env var to persist them. Generate "
            "one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
        return Fernet(Fernet.generate_key())
    return Fernet(key.encode("ascii"))


def encrypt(plaintext: str | None) -> str | None:
    """Encrypt a plaintext string for storage. None → None."""
    if plaintext is None:
        return None
    if plaintext == "":
        return None
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str | None) -> str | None:
    """Decrypt a stored token back to plaintext."""
    if not token:
        return None
    return _fernet().decrypt(token.encode("ascii")).decode("utf-8")


def safe_decrypt(token: str | None) -> str | None:
    """Lenient decrypt — used during the migration backfill so rows
    that were not yet encrypted (any pre-migration row) flow through
    unchanged. Outside the migration, prefer `decrypt()`."""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        return token
