from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.core.config import Settings, settings
from app.core.errors import ErrorCodes
from app.domain.users.models import UserSession
from app.main import app
from tests._factories import signup_user


def test_env_overrides_default(db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("SESSION_IDLE_HOURS", raising=False)
    assert Settings(_env_file=None).SESSION_IDLE_HOURS == 24

    monkeypatch.setenv("SESSION_IDLE_HOURS", "1")
    settings.cache_clear()
    try:
        assert settings().SESSION_IDLE_HOURS == 1

        client = TestClient(app)
        signup_user(client)

        rows = db.query(UserSession).all()
        assert rows
        for row in rows:
            row.last_used_at = datetime.now(timezone.utc) - timedelta(hours=2)
        db.commit()

        response = client.get("/api/auth/me")

        assert response.status_code == 401, response.text
        assert response.json()["code"] == ErrorCodes.AUTH_SESSION_IDLE_TIMEOUT
    finally:
        settings.cache_clear()
