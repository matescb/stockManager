"""Race-condition test for concurrent invitation creates (BE2-020 / #65).

Two admin threads both POST /invitations for the same email at the same
time. The read-then-write used to let both pass the existing-pending
check, after which one would hit uq_workspace_invitation_pending and
surface as an IntegrityError 500. With the savepoint + IntegrityError
handling in place, both must return 2xx and only one pending row must
exist in the database.
"""
from __future__ import annotations

import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


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


def test_concurrent_creates_both_return_2xx_one_row(admin):
    """Two concurrent POST /invitations for the same email must both
    return 2xx, and only one pending row should exist afterwards."""
    invitee_email = f"race-create-{uuid.uuid4().hex[:6]}@x.com"
    cookie = next(c for c in admin.cookies.jar)

    results: list[int] = []
    barrier = threading.Barrier(2)

    def do_create():
        c = TestClient(app)
        c.cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path)
        barrier.wait()
        resp = c.post("/api/invitations", json={"email": invitee_email, "role": "member"})
        results.append(resp.status_code)

    t1 = threading.Thread(target=do_create)
    t2 = threading.Thread(target=do_create)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert len(results) == 2, f"expected 2 results, got {results}"
    # Neither should 500 from IntegrityError.
    assert 500 not in results, f"got 500 in concurrent creates: {results}"
    # Both should be 2xx (201 for new, 200 for collision path).
    for s in results:
        assert s in (200, 201), f"unexpected status {s} in {results}"

    # Verify only one pending row exists.
    rows = admin.get("/api/invitations").json()["data"]
    pending = [r for r in rows if r["email"] == invitee_email and r["status"] == "pending"]
    assert len(pending) == 1, f"expected 1 pending row, got {len(pending)}: {pending}"
