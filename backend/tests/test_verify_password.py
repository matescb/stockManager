from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.users.models import User
from app.main import app


def test_malformed_hash_returns_401(db):
    email = "bad-hash@example.com"
    db.add(User(email=email, name="Bad Hash", password_hash="not-an-argon2-hash"))
    db.commit()

    client = TestClient(app)
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": "TestPass-2026-Stronk"},
    )

    assert response.status_code == 401, response.text
    body = response.json()
    assert body["status"]["category"] == "unauthenticated"
    assert body["code"] == "auth.invalid_credentials"
