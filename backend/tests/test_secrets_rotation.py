from __future__ import annotations

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.core import secrets
from app.core.config import settings


def test_multifernet_decrypts_old_ciphertext(monkeypatch):
    old_key = Fernet.generate_key().decode("ascii")
    new_key = Fernet.generate_key().decode("ascii")
    plaintext = "rotatable-workspace-secret"

    try:
        monkeypatch.setenv("WORKSPACE_SECRETS_KEY", old_key)
        settings.cache_clear()
        secrets._fernet.cache_clear()
        old_ciphertext = secrets.encrypt(plaintext)

        monkeypatch.setenv("WORKSPACE_SECRETS_KEY", f"{new_key},{old_key}")
        settings.cache_clear()
        secrets._fernet.cache_clear()

        assert secrets.decrypt(old_ciphertext) == plaintext

        new_ciphertext = secrets.encrypt(plaintext)
        assert new_ciphertext is not None
        assert Fernet(new_key.encode("ascii")).decrypt(
            new_ciphertext.encode("ascii")
        ).decode("utf-8") == plaintext
        with pytest.raises(InvalidToken):
            Fernet(old_key.encode("ascii")).decrypt(new_ciphertext.encode("ascii"))

    finally:
        settings.cache_clear()
        secrets._fernet.cache_clear()
