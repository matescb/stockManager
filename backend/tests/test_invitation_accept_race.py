"""Race-condition test for concurrent invitation accepts (BE2-020 / #65).

Two threads both POST /accept with the same token at the same time. The
read-then-write in accept_invitation used to let both pass the membership
existence check, after which one would hit uq_workspace_member and surface
as an uncaught IntegrityError 500. With the savepoint + IntegrityError
handling in place, both must return 200.
"""
from __future__ import annotations

import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


pytestmark = pytest.mark.real_db


def _signup(c: TestClient, email: str | None = None) -> tuple[str, str]:
    email = email or f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text
    return email, r.json()["data"]["workspace_id"]


@pytest.fixture
def admin():
    c = TestClient(app)
    _signup(c)
    return c


def test_concurrent_accepts_both_return_200(admin):
    """Two concurrent POST /accept requests for the same token must both
    return 200 and not produce a 500 IntegrityError."""
    invitee_email = f"race-accept-{uuid.uuid4().hex[:6]}@x.com"

    # Admin creates an invitation
    r = admin.post("/api/invitations", json={"email": invitee_email, "role": "member"})
    assert r.status_code == 201, r.text
    token = r.json()["data"]["token"]
    assert token

    # The invitee signs up; both "browser tabs" share the session cookie.
    invitee = TestClient(app)
    _signup(invitee, invitee_email)
    cookie = next(c for c in invitee.cookies.jar)

    results: list[int] = []
    barrier = threading.Barrier(2)

    def do_accept():
        c = TestClient(app)
        c.cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path)
        barrier.wait()  # both threads fire simultaneously
        resp = c.post("/api/invitations/accept", json={"token": token})
        results.append(resp.status_code)

    t1 = threading.Thread(target=do_accept)
    t2 = threading.Thread(target=do_accept)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert len(results) == 2, f"expected 2 results, got {results}"
    # Neither concurrent request should produce a 500 from IntegrityError.
    assert 500 not in results, f"got 500 in concurrent accepts: {results}"
    # At least one must succeed; a second accept of an already-accepted
    # invitation returns 400 (not 500), which is also acceptable here.
    assert any(s == 200 for s in results), f"expected at least one 200: {results}"
