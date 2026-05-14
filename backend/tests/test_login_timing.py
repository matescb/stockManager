from __future__ import annotations

import time
import uuid

from fastapi.testclient import TestClient

from app.api.routes import auth as auth_routes
from tests._factories import signup_user


def test_known_vs_unknown_timing_within_tolerance(
    client: TestClient,
    monkeypatch,
) -> None:
    email = f"timing-{uuid.uuid4().hex[:8]}@x.com"
    signup_user(client, email=email)

    calls: list[str] = []

    def fake_verify_password(hash_: str, password: str) -> bool:
        calls.append(hash_)
        time.sleep(0.05)
        return False

    monkeypatch.setattr(auth_routes, "verify_password", fake_verify_password)

    def fail_login(login_email: str) -> float:
        start = time.perf_counter()
        response = client.post(
            "/api/auth/login",
            json={"email": login_email, "password": "WrongPass!!X"},
        )
        elapsed = time.perf_counter() - start
        assert response.status_code == 401, response.text
        return elapsed

    known_elapsed = fail_login(email)
    unknown_elapsed = fail_login(f"missing-{uuid.uuid4().hex[:8]}@x.com")

    assert len(calls) == 2
    assert calls[0] != auth_routes._DUMMY_ARGON2
    assert calls[1] == auth_routes._DUMMY_ARGON2
    assert abs(known_elapsed - unknown_elapsed) <= 0.03
