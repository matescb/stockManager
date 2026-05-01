"""Pin the password-strength check on signup (Sec MED-4).

The validator runs inside the route, after Pydantic min_length=8 passes.
Both "obvious-breach" passwords and "low-entropy" patterns are rejected
with HTTP 400 carrying a human-readable message the UI can display.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient, *, email: str | None = None, password: str) -> int:
    email = email or f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": password},
    )
    return r.status_code


def test_strong_password_succeeds():
    c = TestClient(app)
    code = _signup(c, password="VeryStrong-2026-Stockmgr!")
    assert code == 200, code


def test_breach_list_password_rejected():
    c = TestClient(app)
    # "password123" is in the inline breach list.
    code = _signup(c, password="password123")
    assert code == 400, code


def test_repetitive_password_rejected():
    c = TestClient(app)
    # 8 chars but only 1 distinct character — fails the entropy check.
    code = _signup(c, password="aaaaaaaa")
    assert code == 400, code


def test_two_distinct_chars_rejected():
    c = TestClient(app)
    # 8 chars, 2 distinct, classic alternation.
    code = _signup(c, password="abababab")
    assert code == 400, code


def test_short_password_rejected_at_schema_layer():
    c = TestClient(app)
    # Pydantic min_length=8 trips first → 422 (not the 400 from the
    # strength check). Either way, signup is blocked.
    code = _signup(c, password="abc12")
    assert code in (400, 422), code


def test_breach_list_is_case_insensitive():
    c = TestClient(app)
    code = _signup(c, password="PASSWORD123")
    assert code == 400, code
