"""Tests for TEST-009 — fixture isolation via SAVEPOINT rollback.

Two ordered tests: the first writes a row, the second tries to write
the same row again. Before the savepoint-rollback fix, the second
would fail with 409 because the first had committed. After the fix,
both succeed because the first test's transaction is rolled back at
teardown.

These pin the per-test isolation contract for HTTP tests through
`client` / `authed_client`, both of which now depend on `db`.
"""
from __future__ import annotations


_ISOLATION_EMAIL = "iso-test-fixture@example.com"


def test_a_writes_a_user(client):
    r = client.post(
        "/api/auth/signup",
        json={"email": _ISOLATION_EMAIL, "name": "Iso A", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text


def test_b_does_not_see_user_from_a(client):
    # Without the savepoint rollback, this would return 409
    # because test_a committed the row to the real DB.
    r = client.post(
        "/api/auth/signup",
        json={"email": _ISOLATION_EMAIL, "name": "Iso B", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text
