"""BE2-004 — per-user owned-workspace cap.

Any authenticated user can create new organisation workspaces, but
not unbounded. The personal workspace minted at signup is excluded
from the cap (it's `kind="personal"`); each subsequent
`POST /api/workspaces` produces a `kind="organization"` row, of
which a user can own at most _OWNED_ORG_WORKSPACE_CAP (= 5).
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.api.routes.workspaces import _OWNED_ORG_WORKSPACE_CAP
from app.main import app


def _signup(c: TestClient) -> None:
    email = f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text


def test_owned_workspace_cap_enforced():
    c = TestClient(app)
    _signup(c)

    # Cap is the number of *additional* org workspaces. Personal one at
    # signup doesn't count.
    for i in range(_OWNED_ORG_WORKSPACE_CAP):
        r = c.post("/api/workspaces", json={"name": f"Ws-{i}"})
        assert r.status_code == 201, (i, r.text)

    # The next one is rejected with 409 + structured detail.
    r = c.post("/api/workspaces", json={"name": "Ws-overflow"})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["status"]["category"] == "conflict"
    assert body.get("existing_count") == _OWNED_ORG_WORKSPACE_CAP
    assert body.get("cap") == _OWNED_ORG_WORKSPACE_CAP


def test_signup_personal_workspace_not_counted():
    """A fresh user can create the full cap of orgs; the personal
    workspace from signup is `kind="personal"` and excluded."""
    c = TestClient(app)
    _signup(c)
    # Confirm the user starts with one membership (the personal ws).
    me = c.get("/api/auth/me").json()["data"]
    assert len(me["workspaces"]) == 1
    # Creating one org should still be allowed (1/5 of the cap, not
    # 2/5 because the personal one would have been counted).
    r = c.post("/api/workspaces", json={"name": "Ws-1"})
    assert r.status_code == 201, r.text
