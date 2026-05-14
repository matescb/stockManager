from __future__ import annotations

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from app.core.config import settings
from app.domain.users.models import User
from app.main import app


def test_old_params_hash_rotates_on_login(db):
    email = "old-argon2@example.com"
    password = "TestPass-2026-Stronk"
    old_hasher = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1, hash_len=16, salt_len=8)
    old_hash = old_hasher.hash(password)
    assert PasswordHasher().check_needs_rehash(old_hash)

    user = User(email=email, name="Old Params", password_hash=old_hash)
    db.add(user)
    db.commit()

    client = TestClient(app)
    response = client.post("/api/auth/login", json={"email": email, "password": password})

    assert response.status_code == 200, response.text
    db.refresh(user)
    assert user.password_hash != old_hash
    assert not PasswordHasher().check_needs_rehash(user.password_hash)
    assert client.cookies.get(settings().SESSION_COOKIE_NAME)
