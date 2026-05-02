"""Pin the public contract of `app.core.secrets` against the frozen
shim `app.core._secrets_v0016` so a future rename / refactor of the
live module surfaces as a CI failure rather than a migration-time
crash.

Migration `0016_encrypt_workspace_secrets.py:85` does
`from app.core.secrets import encrypt, safe_decrypt`. If the live
module's signature changes, the migration replays break (DB-010).
This test detects the divergence and prompts updating the frozen
shim AND the migration as a coordinated change.
"""
from __future__ import annotations

import inspect

from app.core import _secrets_v0016 as frozen
from app.core import secrets as live


def test_encrypt_signatures_match() -> None:
    assert inspect.signature(live.encrypt) == inspect.signature(frozen.encrypt), (
        "app.core.secrets.encrypt has drifted from the frozen shim "
        "_secrets_v0016 — update both, then update migration 0016."
    )


def test_safe_decrypt_signatures_match() -> None:
    assert inspect.signature(live.safe_decrypt) == inspect.signature(
        frozen.safe_decrypt
    ), (
        "app.core.secrets.safe_decrypt has drifted from the frozen shim "
        "_secrets_v0016 — update both, then update migration 0016."
    )


def test_encrypt_decrypt_roundtrip_in_both_modules() -> None:
    """Each module must roundtrip its own ciphertexts.

    Note: cross-module decrypt is intentionally NOT asserted here.
    Each module's `_fernet()` is independently lru-cached, so under
    the dev/test fallback (no `WORKSPACE_SECRETS_KEY` env var) they
    mint distinct ephemeral keys. In prod, both modules read the
    same env var and share keying — but the unit-test fallback
    generates per-cache ephemeral keys, so cross-module decrypt
    can't be asserted without a fixture that primes the env var.
    The signature pin above is the load-bearing contract; this
    roundtrip just sanity-checks each module works in isolation."""
    plaintext = "hunter2-secret"
    live_ct = live.encrypt(plaintext)
    frozen_ct = frozen.encrypt(plaintext)
    assert live_ct is not None
    assert frozen_ct is not None
    assert live.decrypt(live_ct) == plaintext
    assert frozen.decrypt(frozen_ct) == plaintext
