"""SEC2-004 — workspace switch hardening.

The /workspaces/{id}/switch endpoint pre-fix was unauthenticated, didn't
parse `workspace_id`, and didn't check membership. Now: typed UUID,
requires CurrentUser, 404s unless the caller has an active membership.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient, email: str | None = None) -> str:
    email = email or f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["workspace_id"]


def test_switch_unauthenticated_rejected():
    c = TestClient(app)
    fake_ws = uuid.uuid4()
    r = c.post(f"/api/workspaces/{fake_ws}/switch")
    assert r.status_code == 401, r.text


def test_switch_unknown_workspace_404():
    c = TestClient(app)
    _signup(c)
    fake_ws = uuid.uuid4()
    r = c.post(f"/api/workspaces/{fake_ws}/switch")
    assert r.status_code == 404, r.text


def test_switch_non_member_workspace_404():
    """A signed-up user trying to switch into a workspace they don't
    belong to must get a 404 — not a leak of membership info."""
    a = TestClient(app)
    _signup(a)
    b = TestClient(app)
    target_ws = _signup(b)

    r = a.post(f"/api/workspaces/{target_ws}/switch")
    assert r.status_code == 404, r.text


def test_switch_happy_path_sets_strict_cookie():
    c = TestClient(app)
    ws_id = _signup(c)
    r = c.post(f"/api/workspaces/{ws_id}/switch")
    assert r.status_code == 200, r.text
    # The Set-Cookie response carries SameSite=Strict (v1 Sec CRIT-4).
    set_cookie = r.headers.get("set-cookie") or ""
    assert "stockmgr_workspace=" in set_cookie
    assert "samesite=strict" in set_cookie.lower()


def test_switch_rejects_non_uuid_path():
    c = TestClient(app)
    _signup(c)
    # FastAPI's path-param parsing rejects non-UUID with 422.
    r = c.post("/api/workspaces/not-a-uuid/switch")
    assert r.status_code == 422, r.text
