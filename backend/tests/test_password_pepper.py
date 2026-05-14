from __future__ import annotations

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from app.core.auth import hash_password, verify_password
from app.core.config import settings
from app.domain.users.models import User
from app.main import app


def test_password_hash_uses_server_side_pepper(monkeypatch):
    cfg = settings()
    monkeypatch.setattr(cfg, "PASSWORD_PEPPER", "test-pepper")

    password = "TestPass-2026-Stronk"
    password_hash = hash_password(password)

    assert verify_password(password_hash, password) is True
    with pytest.raises(Exception):
        PasswordHasher().verify(password_hash, password)


def test_legacy_unpeppered_hash_rehashes_with_pepper_on_login(db, monkeypatch):
    cfg = settings()
    monkeypatch.setattr(cfg, "PASSWORD_PEPPER", "test-pepper")
    email = "legacy-pepper@example.com"
    password = "TestPass-2026-Stronk"
    legacy_hash = PasswordHasher().hash(password)
    user = User(email=email, name="Legacy Pepper", password_hash=legacy_hash)
    db.add(user)
    db.commit()

    client = TestClient(app)
    response = client.post("/api/auth/login", json={"email": email, "password": password})

    assert response.status_code == 200, response.text
    db.refresh(user)
    assert user.password_hash != legacy_hash
    assert verify_password(user.password_hash, password) is True
    with pytest.raises(Exception):
        PasswordHasher().verify(user.password_hash, password)


def test_password_pepper_required_in_prod(monkeypatch):
    from cryptography.fernet import Fernet
    from pydantic import ValidationError

    from app.core.config import Settings

    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("WORKSPACE_SECRETS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("PASSWORD_PEPPER", "")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "user")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    monkeypatch.setenv("MAIL_FROM", "noreply@example.com")
    monkeypatch.setenv("APP_BASE_URL", "https://parts.example.com")

    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert "PASSWORD_PEPPER" in str(exc_info.value)
