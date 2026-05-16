from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

import app.core.ratelimit as _ratelimit_mod
from app.main import app
from tests._factories import signup_user


@pytest.fixture
def limiter_enabled():
    original = _ratelimit_mod.limiter.enabled
    _ratelimit_mod.limiter.enabled = True
    try:
        _ratelimit_mod.limiter.reset()
    except Exception:
        pass
    yield
    _ratelimit_mod.limiter.enabled = original
    try:
        _ratelimit_mod.limiter.reset()
    except Exception:
        pass


def _signup_admin(email: str | None = None) -> TestClient:
    client = TestClient(app)
    signup_user(client, email=email or f"admin-{uuid.uuid4().hex[:8]}@x.com")
    return client


def _create_invitation(client: TestClient, email: str | None = None):
    return client.post(
        "/api/invitations",
        json={
            "email": email or f"invitee-{uuid.uuid4().hex[:8]}@x.com",
            "role": "member",
        },
    )


def test_create_invitation_rate_limited_after_60_per_hour(db, limiter_enabled):
    admin = _signup_admin()

    for i in range(60):
        response = _create_invitation(admin, f"create-{i}-{uuid.uuid4().hex[:8]}@x.com")
        assert response.status_code == 201, response.text

    response = _create_invitation(admin, f"create-overflow-{uuid.uuid4().hex[:8]}@x.com")
    assert response.status_code == 429, response.text
    assert response.json()["status"]["category"] == "rate_limited"


def test_list_invitations_rate_limit_is_per_workspace(db, limiter_enabled):
    workspace_a = _signup_admin()
    workspace_b = _signup_admin()

    for i in range(120):
        response = workspace_a.get("/api/invitations")
        assert response.status_code == 200, f"call {i}: {response.status_code} {response.text}"

    response = workspace_a.get("/api/invitations")
    assert response.status_code == 429, response.text
    assert response.json()["status"]["category"] == "rate_limited"

    response = workspace_b.get("/api/invitations")
    assert response.status_code == 200, response.text


def test_revoke_invitation_rate_limited_after_120_per_hour(db, limiter_enabled):
    admin = _signup_admin()
    invitation = _create_invitation(admin).json()["data"]

    for i in range(120):
        response = admin.delete(f"/api/invitations/{invitation['id']}")
        assert response.status_code != 429, f"call {i}: {response.status_code} {response.text}"

    response = admin.delete(f"/api/invitations/{invitation['id']}")
    assert response.status_code == 429, response.text
    assert response.json()["status"]["category"] == "rate_limited"
