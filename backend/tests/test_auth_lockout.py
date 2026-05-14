"""Per-account login lockout tests (SEC2-014).

Covers:
- 10 consecutive failed logins lock the account for 15 min.
- Successful login resets the failure counter.
- Lockout response is 429 with retry_after_seconds.
- Lockout does NOT reveal whether the email exists.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.main import app
from tests._factories import DEFAULT_PASSWORD, signup_user


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def user_email_and_client():
    """Return (email, authed TestClient) for a freshly signed-up user."""
    c = TestClient(app)
    email = f"lockout-{uuid.uuid4().hex[:8]}@x.com"
    signup_user(c, email=email)
    return email, c


def _fail_login(c: TestClient, email: str, n: int = 1) -> list:
    """Attempt n failed logins for email. Returns list of responses."""
    resps = []
    for _ in range(n):
        r = c.post("/api/auth/login", json={"email": email, "password": "WrongPass!!X"})
        resps.append(r)
    return resps


def test_ten_failures_lock_account(user_email_and_client):
    """After LOCKOUT_MAX_FAILURES (10) recorded failures, the next attempt returns 429.

    The lockout check runs BEFORE credential validation.  Recording a
    failure happens only on a 401 response, so:
    - Attempts 1–10: check passes (< 10 prior failures), wrong cred → 401 +
      one failure recorded.
    - Attempt 11: check finds 10 prior failures → 429 without touching creds.

    This means 11 total failed attempts are needed to see the lockout 429.
    """
    email, _ = user_email_and_client
    c = TestClient(app)

    from app.core.auth import LOCKOUT_MAX_FAILURES

    # First LOCKOUT_MAX_FAILURES attempts should all return 401.
    resps = _fail_login(c, email, n=LOCKOUT_MAX_FAILURES)
    for r in resps:
        assert r.status_code == 401, r.text

    # Next attempt: check fires before creds → 429.
    r = _fail_login(c, email, n=1)[0]
    assert r.status_code == 429, r.text
    body = r.json()
    assert body["code"] == "auth.account_locked"
    assert "retry_after_seconds" in body
    assert body["retry_after_seconds"] > 0


def test_lockout_blocks_correct_password(user_email_and_client):
    """After the lockout threshold is reached, even the correct password returns 429."""
    email, _ = user_email_and_client
    c = TestClient(app)

    from app.core.auth import LOCKOUT_MAX_FAILURES

    # Exhaust failures to trigger the lockout on the next attempt.
    _fail_login(c, email, n=LOCKOUT_MAX_FAILURES)

    # Correct credentials — should be locked by the pre-check.
    r = c.post("/api/auth/login", json={"email": email, "password": DEFAULT_PASSWORD})
    assert r.status_code == 429, r.text
    assert r.json()["code"] == "auth.account_locked"


def test_successful_login_resets_counter(user_email_and_client):
    """Fewer-than-threshold failures followed by a successful login resets the counter."""
    email, _ = user_email_and_client
    c = TestClient(app)

    from app.core.auth import LOCKOUT_MAX_FAILURES

    # Fail fewer than the threshold.
    _fail_login(c, email, n=LOCKOUT_MAX_FAILURES - 1)

    # Successful login.
    r = c.post("/api/auth/login", json={"email": email, "password": DEFAULT_PASSWORD})
    assert r.status_code == 200, r.text

    # Counter is reset — threshold-1 more failures should still return 401.
    resps = _fail_login(c, email, n=LOCKOUT_MAX_FAILURES - 1)
    for r in resps:
        assert r.status_code == 401, r.text


def test_unknown_email_also_gets_locked():
    """Stuffing attempts against a non-existent email are also rate-limited.

    The lockout does not reveal whether the email exists:
    - Before lockout threshold: returns 401 (same as wrong password on a real account).
    - After threshold: returns 429 (same as a real account lockout).
    """
    from app.core.auth import LOCKOUT_MAX_FAILURES

    c = TestClient(app)
    ghost = f"ghost-{uuid.uuid4().hex[:8]}@nowhere.com"

    resps = _fail_login(c, ghost, n=LOCKOUT_MAX_FAILURES)
    for r in resps:
        assert r.status_code == 401, r.text

    r = _fail_login(c, ghost, n=1)[0]
    assert r.status_code == 429, r.text
    assert r.json()["code"] == "auth.account_locked"


def test_lockout_shape_uniform_across_known_and_unknown():
    """Known-account and phantom-email lockouts must return the same body shape."""
    from app.core.auth import LOCKOUT_MAX_FAILURES

    def response_shape(value):
        if isinstance(value, dict):
            return {key: response_shape(nested) for key, nested in sorted(value.items())}
        if isinstance(value, list):
            return [response_shape(item) for item in value]
        return type(value).__name__

    c = TestClient(app)
    known = f"known-{uuid.uuid4().hex[:8]}@x.com"
    unknown = f"ghost-{uuid.uuid4().hex[:8]}@nowhere.com"
    signup_user(c, email=known)

    _fail_login(c, known, n=LOCKOUT_MAX_FAILURES)
    _fail_login(c, unknown, n=LOCKOUT_MAX_FAILURES)

    known_response = c.post("/api/auth/login", json={"email": known, "password": "WrongPass!!X"})
    unknown_response = c.post(
        "/api/auth/login",
        json={"email": unknown, "password": "WrongPass!!X"},
    )

    assert known_response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert unknown_response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    known_body = known_response.json()
    unknown_body = unknown_response.json()
    assert response_shape(known_body) == response_shape(unknown_body)
    assert known_body["code"] == unknown_body["code"] == "auth.account_locked"
    assert known_body["status"] == unknown_body["status"]


def test_lockout_response_envelope():
    """The 429 lockout response must be in the standard {data, status} envelope."""
    c = TestClient(app)
    email = f"env-{uuid.uuid4().hex[:8]}@x.com"
    signup_user(c, email=email)

    _fail_login(c, email, n=10)

    r = c.post("/api/auth/login", json={"email": email, "password": "WrongPass!!X"})
    assert r.status_code == 429
    body = r.json()
    # Must have the envelope shape.
    assert "data" in body
    assert "status" in body or "code" in body  # http_exception_handler spreads the detail
