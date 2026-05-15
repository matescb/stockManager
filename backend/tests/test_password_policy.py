from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


def test_extra_weak_passwords_rejected(monkeypatch):
    monkeypatch.setenv("EXTRA_WEAK_PASSWORDS", "CustomerBrand2026, internal-code-name")
    settings.cache_clear()

    try:
        assert settings().EXTRA_WEAK_PASSWORDS == [
            "CustomerBrand2026",
            "internal-code-name",
        ]

        client = TestClient(app)
        response = client.post(
            "/api/auth/signup",
            json={
                "email": "extra-weak-password@example.com",
                "name": "Policy Test",
                "password": "customerbrand2026",
            },
        )

        assert response.status_code == 400
        assert "password" in response.text.lower()
    finally:
        settings.cache_clear()
