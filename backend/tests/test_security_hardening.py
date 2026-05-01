"""Regression tests for the Tier B security hardening batch.

Pins:
- workspace-switch cookie attributes (httponly + samesite + secure-in-prod)
- OpenAPI docs are reachable in dev (the prod-disable path is verified by
  the constructor logic; we don't reload the module in tests)
- Sentry `before_send` scrubber strips request body on workspace settings
  PATCH/switch and strips tenant-identifying headers
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import _scrub_event, app


def _signup(c: TestClient) -> str:
    email = f"sec-{uuid.uuid4().hex[:6]}@x.com"
    r = c.post("/api/auth/signup", json={"email": email, "name": "u", "password": "TestPass-2026-Stronk"})
    assert r.status_code == 200, r.text
    return r.json()["data"]["workspace_id"]


# ---------------------------------------------------------------------------
# Workspace cookie hardening (Sec CRIT-3 in 2026-04-30 review)
# ---------------------------------------------------------------------------


def test_workspace_switch_sets_hardened_cookie_attributes():
    c = TestClient(app)
    ws_id = _signup(c)
    r = c.post(f"/api/workspaces/{ws_id}/switch")
    assert r.status_code == 200, r.text
    set_cookie = r.headers.get("set-cookie", "")
    assert set_cookie, "expected a Set-Cookie header"
    lower = set_cookie.lower()
    # httponly: blocks JS read of the cookie. Reverses Sec CRIT-3.
    assert "httponly" in lower, f"missing HttpOnly: {set_cookie}"
    # samesite=lax: blocks cross-site cookie attachment on non-GET.
    assert "samesite=lax" in lower, f"missing SameSite=Lax: {set_cookie}"
    # In test env APP_ENV defaults to "dev" so Secure is correctly absent.
    # The prod path (`Secure` set when APP_ENV == "prod") is exercised by
    # reading the route's logic — covered by code review, not this test.


# ---------------------------------------------------------------------------
# OpenAPI surface (Sec MED-1)
# ---------------------------------------------------------------------------


def test_openapi_docs_enabled_in_dev():
    """In test env APP_ENV defaults to 'dev' so /docs is mounted. The prod
    path (`docs_url=None` when APP_ENV=='prod') is set at FastAPI()
    construction; we don't module-reload here. If a later refactor moves
    the gate, this test catches the dev-side regression."""
    c = TestClient(app)
    r = c.get("/docs")
    assert r.status_code == 200, r.text
    r = c.get("/openapi.json")
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Sentry before_send scrubber (Sec HIGH-1)
# ---------------------------------------------------------------------------


def test_scrubber_strips_patch_body_on_workspace_settings():
    event = {
        "request": {
            "method": "PATCH",
            "url": "https://parts.matescb.cz/api/workspaces/current",
            "data": '{"parts_provider_api_key": "SECRET"}',
            "headers": {"Content-Type": "application/json"},
        },
    }
    out = _scrub_event(event, None)
    assert "data" not in out["request"]
    assert out["request"].get("body_redacted") is True


def test_scrubber_strips_post_body_on_workspace_switch():
    event = {
        "request": {
            "method": "POST",
            "url": "https://parts.matescb.cz/api/workspaces/abc/switch",
            "data": "anything",
            "headers": {},
        },
    }
    out = _scrub_event(event, None)
    assert "data" not in out["request"]


def test_scrubber_strips_body_on_unrelated_post_too():
    """Updated for v2 SEC2-005: the previous URL allow-list (only
    `/api/workspaces`) leaked credential-bearing bodies on signup,
    login, invitations, parts-provider, bulk-import. The new posture
    is method-based default-deny — every non-GET strips its body."""
    event = {
        "request": {
            "method": "POST",
            "url": "https://parts.matescb.cz/api/parts",
            "data": '{"name": "Cap"}',
            "headers": {},
        },
    }
    out = _scrub_event(event, None)
    assert "data" not in out["request"]
    assert out["request"].get("body_redacted") is True


def test_scrubber_strips_sensitive_headers():
    event = {
        "request": {
            "method": "GET",
            "url": "https://parts.matescb.cz/api/parts",
            "headers": {
                "Cookie": "session=abc; stockmgr_workspace=xyz",
                "Authorization": "Bearer foo",
                "X-Workspace-Id": "00000000-0000-0000-0000-000000000000",
                "User-Agent": "Mozilla/5.0",
                "Content-Type": "application/json",
            },
        },
    }
    out = _scrub_event(event, None)
    headers = out["request"]["headers"]
    assert "Cookie" not in headers
    assert "Authorization" not in headers
    assert "X-Workspace-Id" not in headers
    # Non-sensitive headers kept for triage value.
    assert headers["User-Agent"] == "Mozilla/5.0"
    assert headers["Content-Type"] == "application/json"


def test_scrubber_strips_lowercased_headers_too():
    """Sentry's HTTPx integration sometimes lowercases header names."""
    event = {
        "request": {
            "method": "GET",
            "url": "https://parts.matescb.cz/api/parts",
            "headers": {
                "cookie": "session=abc",
                "authorization": "Bearer foo",
            },
        },
    }
    out = _scrub_event(event, None)
    assert "cookie" not in out["request"]["headers"]
    assert "authorization" not in out["request"]["headers"]


def test_scrubber_handles_event_without_request():
    """Some Sentry events are diagnostic / breadcrumb-only. Don't crash."""
    out = _scrub_event({"message": "hello"}, None)
    assert out == {"message": "hello"}
    out = _scrub_event({"request": "not a dict"}, None)
    assert out["request"] == "not a dict"
