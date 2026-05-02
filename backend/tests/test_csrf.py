"""SEC2-001 — CSRF Origin/Referer middleware.

State-changing requests must carry an `Origin` (or fallback `Referer`)
that resolves to one of the configured CORS allow-list entries. Reads
(GET / HEAD / OPTIONS) are unaffected. The login / signup / sentry
tunnel paths are explicitly exempt — see app/main.py for why.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient) -> str:
    email = f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text
    return email


def test_same_origin_post_passes():
    """conftest patches TestClient to send Origin: http://testserver,
    which is in the allow-list. Plain authenticated POST must succeed."""
    c = TestClient(app)
    _signup(c)
    r = c.post("/api/parts", json={"name": "Cap", "part_type": "local"})
    assert r.status_code in (200, 201), r.text


def test_cross_origin_post_blocked():
    c = TestClient(app)
    _signup(c)
    r = c.post(
        "/api/parts",
        json={"name": "Cap", "part_type": "local"},
        headers={"Origin": "https://evil.example.com"},
    )
    assert r.status_code == 403, r.text


def test_missing_origin_on_patch_blocked():
    c = TestClient(app)
    _signup(c)
    part_id = c.post("/api/parts", json={"name": "Cap", "part_type": "local"}).json()["data"]["id"]
    # Wipe any Origin / Referer entirely on the request.
    r = c.patch(
        f"/api/parts/{part_id}",
        json={"name": "Cap2"},
        headers={"Origin": "", "Referer": ""},
    )
    assert r.status_code == 403, r.text


def test_get_unaffected_by_csrf():
    c = TestClient(app)
    _signup(c)
    # GET with a hostile Origin still passes — CSRF only fires on state changers.
    r = c.get(
        "/api/auth/me",
        headers={"Origin": "https://evil.example.com"},
    )
    assert r.status_code == 200, r.text


def test_login_exempt_from_csrf():
    """Login is pre-auth; the threat is brute force, handled by slowapi.
    A foreign-origin POST to /api/auth/login must not be 403'd by CSRF."""
    email = f"u-{uuid.uuid4().hex[:8]}@x.com"
    pw = "TestPass-2026-Stronk"
    setup = TestClient(app)
    setup.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": pw},
    )

    foreign = TestClient(app)
    r = foreign.post(
        "/api/auth/login",
        json={"email": email, "password": pw},
        headers={"Origin": "https://evil.example.com"},
    )
    # Login itself must succeed; CSRF doesn't gate pre-auth endpoints.
    assert r.status_code == 200, r.text


def test_referer_fallback_accepted():
    c = TestClient(app)
    _signup(c)
    # No Origin, but Referer points at an allow-listed origin → pass.
    r = c.post(
        "/api/parts",
        json={"name": "Resistor", "part_type": "local"},
        headers={"Origin": "", "Referer": "http://testserver/dashboard"},
    )
    assert r.status_code in (200, 201), r.text


def test_referer_to_evil_origin_blocked():
    c = TestClient(app)
    _signup(c)
    r = c.post(
        "/api/parts",
        json={"name": "Resistor", "part_type": "local"},
        headers={"Origin": "", "Referer": "https://evil.example.com/page"},
    )
    assert r.status_code == 403, r.text
