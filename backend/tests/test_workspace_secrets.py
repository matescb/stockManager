"""Tests for the workspace-secrets-at-rest encryption (Sec HIGH-9, redo).

Pins:
  - encrypt(plaintext) -> decrypt round-trips.
  - encrypt(None) and encrypt("") collapse to None (matches the route's
    "empty payload clears the credential" semantics).
  - The DB column carries ciphertext, not plaintext, after a PATCH.
  - The /current/scanner-license-key endpoint returns the plaintext
    (decrypted at the boundary) so the SDK can consume it.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.secrets import decrypt, encrypt
from app.main import app


def test_encrypt_decrypt_roundtrip():
    plain = "RC0402JR-070R-fake-key-1234567890"
    cipher = encrypt(plain)
    assert cipher is not None
    assert cipher != plain  # must not store as-is
    assert decrypt(cipher) == plain


def test_encrypt_none_and_empty_collapse_to_none():
    assert encrypt(None) is None
    assert encrypt("") is None


def test_decrypt_none_and_empty_passthrough():
    assert decrypt(None) is None
    assert decrypt("") is None


@pytest.fixture
def admin():
    c = TestClient(app)
    c.post(
        "/api/auth/signup",
        json={"email": f"u-{uuid.uuid4().hex[:8]}@x.com", "name": "u", "password": "TestPass-2026-Stronk"},
    )
    return c


def test_patch_workspace_stores_credentials_encrypted(admin):
    """PATCH /api/workspaces/current with a Mouser API key. The DB
    column must contain a Fernet token, NOT the plaintext."""
    plaintext = "MOUSER-FAKE-KEY-DEADBEEF-1234567890"
    r = admin.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": plaintext},
    )
    assert r.status_code == 200, r.text
    ws_id = admin.get("/api/workspaces/current").json()["data"]["id"]

    from app.domain.workspaces.models import Workspace
    from app.infra.db import SessionLocal

    with SessionLocal() as s:
        ws = s.get(Workspace, uuid.UUID(ws_id))
        assert ws is not None
        stored = ws.parts_provider_api_key
        assert stored is not None
        assert stored != plaintext, "stored value must not be the plaintext"
        assert decrypt(stored) == plaintext


def test_scanner_license_key_endpoint_returns_decrypted_plaintext(admin):
    """The /current/scanner-license-key endpoint must decrypt at the
    boundary so the SDK gets the plaintext it expects."""
    plaintext = "SCANDIT-FAKE-LICENSE-" + ("X" * 100)
    r = admin.patch(
        "/api/workspaces/current",
        json={"scanner": "scandit", "scanner_license_key": plaintext},
    )
    assert r.status_code == 200, r.text

    r = admin.get("/api/workspaces/current/scanner-license-key")
    assert r.status_code == 200
    assert r.json()["data"]["license_key"] == plaintext


def test_clearing_credential_with_empty_string(admin):
    """Empty-string payload still clears the column (the existing
    semantics — encrypt('') returns None)."""
    admin.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": "ABC"},
    )
    admin.patch(
        "/api/workspaces/current",
        json={"parts_provider_api_key": ""},
    )
    out = admin.get("/api/workspaces/current").json()["data"]
    assert out["has_parts_provider_api_key"] is False


def test_dev_default_key_does_not_crash_when_env_unset(admin):
    """The previous attempt at this work raised RuntimeError when
    WORKSPACE_SECRETS_KEY was unset. Pin that the new posture is a
    soft warning + ephemeral per-process key — round-trip works without
    env (within a single process)."""
    plaintext = "DEV-FALLBACK-OK"
    cipher = encrypt(plaintext)
    assert cipher is not None
    assert decrypt(cipher) == plaintext


def test_prod_with_empty_workspace_secrets_key_fails_closed(monkeypatch):
    """v2 teardown INFRA2-004 / SEC2-002: prod must refuse to start
    when WORKSPACE_SECRETS_KEY is empty, so a misconfigured deploy
    surfaces immediately instead of silently encrypting under a
    fallback. Settings is constructed directly here (bypasses the
    lru_cache on `settings()`) to exercise the validator."""
    from pydantic import ValidationError

    from app.core.config import Settings

    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("WORKSPACE_SECRETS_KEY", "")

    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert "WORKSPACE_SECRETS_KEY" in str(exc_info.value)


def test_prod_with_valid_workspace_secrets_key_boots(monkeypatch):
    """The other direction: a real Fernet key in prod must pass
    validation cleanly. Also populates the SMTP_* and APP_BASE_URL vars
    required by the issue #281 fail-closed validator
    (`_require_smtp_in_prod`) so this test exercises only the
    workspace-secrets-key gate."""
    from cryptography.fernet import Fernet

    from app.core.config import Settings

    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("WORKSPACE_SECRETS_KEY", Fernet.generate_key().decode())
    # Required by `_require_smtp_in_prod` (issue #281).
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "user")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    monkeypatch.setenv("MAIL_FROM", "noreply@example.com")
    monkeypatch.setenv("APP_BASE_URL", "https://parts.example.com")

    s = Settings()
    assert s.APP_ENV == "prod"
    assert s.WORKSPACE_SECRETS_KEY


def test_dev_with_empty_workspace_secrets_key_boots(monkeypatch):
    """Dev posture is unchanged: empty key allowed, ephemeral fallback
    in `core/secrets._fernet`."""
    from app.core.config import Settings

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("WORKSPACE_SECRETS_KEY", "")

    s = Settings()
    assert s.APP_ENV == "dev"
    assert s.WORKSPACE_SECRETS_KEY == ""
