"""Negative-shape contract tests for the error envelope.

Companion to the (eventual) success-envelope tests under #110. Every
error response from a route migrated to `app.core.errors.raise_http`
should produce a body with:

  {
    "data": null,
    "status": {"category": <derived-from-status-code>, "message": <str>},
    "code": <stable-machine-readable-string>,
    "message": <human-readable-string>,
    ...optional spread fields (e.g. existing_id on a 409)
  }

The `category` axis comes from `core/responses._category_for_status`;
`code` is the stable string the FE switches on. They are intentionally
orthogonal — `code` is finer-grained, `category` is the broad bucket.

This file pins the contract for the PR1 layer of issue #125 (auth +
workspaces + invitations + sentry_tunnel). PR2 / PR3 should extend it
with assertions over their migrated routes.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _assert_error_envelope(body: dict, *, expected_category: str, expected_code: str) -> None:
    assert body.get("data") is None, f"expected data=None, got {body!r}"
    status_block = body.get("status")
    assert isinstance(status_block, dict), f"expected dict status, got {body!r}"
    assert status_block.get("category") == expected_category, body
    assert isinstance(status_block.get("message"), str) and status_block["message"], body
    assert body.get("code") == expected_code, body
    assert isinstance(body.get("message"), str) and body["message"], body


def test_auth_login_invalid_credentials_envelope():
    c = TestClient(app)
    r = c.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "bad-password-bad-bad"},
    )
    assert r.status_code == 401
    _assert_error_envelope(
        r.json(),
        expected_category="unauthenticated",
        expected_code="auth.invalid_credentials",
    )


def test_auth_signup_weak_password_envelope():
    c = TestClient(app)
    r = c.post(
        "/api/auth/signup",
        json={"email": f"u-{uuid.uuid4().hex[:6]}@x.com", "name": "U", "password": "short"},
    )
    assert r.status_code == 400
    _assert_error_envelope(
        r.json(),
        expected_category="validation_error",
        expected_code="auth.weak_password",
    )


def test_auth_signup_email_taken_envelope():
    email = f"u-{uuid.uuid4().hex[:6]}@x.com"
    c1 = TestClient(app)
    r1 = c1.post(
        "/api/auth/signup",
        json={"email": email, "name": "U", "password": "TestPass-2026-Stronk"},
    )
    assert r1.status_code == 200
    c2 = TestClient(app)
    r2 = c2.post(
        "/api/auth/signup",
        json={"email": email, "name": "U2", "password": "TestPass-2026-Stronk"},
    )
    assert r2.status_code == 409
    _assert_error_envelope(
        r2.json(),
        expected_category="conflict",
        expected_code="auth.email_taken",
    )


def test_workspace_switch_unknown_id_envelope():
    c = TestClient(app)
    c.post(
        "/api/auth/signup",
        json={
            "email": f"u-{uuid.uuid4().hex[:6]}@x.com",
            "name": "U",
            "password": "TestPass-2026-Stronk",
        },
    )
    r = c.post(f"/api/workspaces/{uuid.uuid4()}/switch")
    assert r.status_code == 404
    _assert_error_envelope(
        r.json(),
        expected_category="not_found",
        expected_code="workspace.not_found",
    )


def test_workspace_remove_nonexistent_member_envelope():
    c = TestClient(app)
    c.post(
        "/api/auth/signup",
        json={
            "email": f"u-{uuid.uuid4().hex[:6]}@x.com",
            "name": "U",
            "password": "TestPass-2026-Stronk",
        },
    )
    r = c.delete(f"/api/workspaces/members/{uuid.uuid4()}")
    assert r.status_code == 404
    _assert_error_envelope(
        r.json(),
        expected_category="not_found",
        expected_code="workspace.member_not_found",
    )


def test_invitation_revoke_unknown_envelope():
    c = TestClient(app)
    c.post(
        "/api/auth/signup",
        json={
            "email": f"u-{uuid.uuid4().hex[:6]}@x.com",
            "name": "U",
            "password": "TestPass-2026-Stronk",
        },
    )
    r = c.delete(f"/api/invitations/{uuid.uuid4()}")
    assert r.status_code == 404
    _assert_error_envelope(
        r.json(),
        expected_category="not_found",
        expected_code="invitation.not_found",
    )


def test_invitation_accept_unknown_token_envelope():
    c = TestClient(app)
    c.post(
        "/api/auth/signup",
        json={
            "email": f"u-{uuid.uuid4().hex[:6]}@x.com",
            "name": "U",
            "password": "TestPass-2026-Stronk",
        },
    )
    r = c.post("/api/invitations/accept", json={"token": "definitely-not-a-real-token"})
    assert r.status_code == 404
    _assert_error_envelope(
        r.json(),
        expected_category="not_found",
        expected_code="invitation.not_found",
    )


@pytest.mark.parametrize("missing_field", ["email", "password"])
def test_pydantic_422_does_not_have_code_field(missing_field):
    """Pydantic-generated 422s flow through `validation_exception_handler`,
    not `raise_http`. They have no `code` field — that's correct and
    expected; this test pins the difference so we don't accidentally
    blur the boundary."""
    c = TestClient(app)
    payload = {"email": "x@y.com", "password": "anything"}
    payload.pop(missing_field)
    r = c.post("/api/auth/login", json=payload)
    assert r.status_code == 422
    body = r.json()
    assert body.get("data") is None
    assert body.get("status", {}).get("category") == "validation_error"
    assert "code" not in body
