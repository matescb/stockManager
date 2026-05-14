"""Tests for the Sentry `before_send` scrubber (v2 teardown SEC2-005).

The scrubber is mounted as `before_send` in `_init_sentry`. In tests we
import the function directly and feed it constructed event payloads —
no Sentry SDK wiring is needed. Pinning here matters because the prior
posture (URL allow-list of /api/workspaces) was identified as leaking
credential-bearing bodies on every other route that handles a secret.
"""
from __future__ import annotations

from app.main import _scrub_event


def _event(method: str, url: str, *, data=None, headers=None):
    req: dict = {"method": method, "url": url}
    if data is not None:
        req["data"] = data
    if headers is not None:
        req["headers"] = headers
    return {"request": req}


# ---------------------------------------------------------------------------
# Body default-deny on non-GET
# ---------------------------------------------------------------------------


def test_scrubber_strips_body_on_signup_post():
    """The original SEC2-005 leak vector: 5xx during signup ships
    plaintext password to Sentry under the prior narrow allow-list."""
    event = _event(
        "POST",
        "/api/auth/signup",
        data={"email": "u@x.com", "name": "u", "password": "TestPass-2026-Stronk"},
    )
    out = _scrub_event(event, None)
    assert "data" not in out["request"]
    assert out["request"]["body_redacted"] is True


def test_scrubber_strips_body_on_login_post():
    event = _event("POST", "/api/auth/login", data={"email": "u@x.com", "password": "p"})
    out = _scrub_event(event, None)
    assert "data" not in out["request"]
    assert out["request"]["body_redacted"] is True


def test_scrubber_strips_body_on_invitation_accept():
    """Raw invitation token is bearer-equivalent until consumed."""
    event = _event("POST", "/api/invitations/accept", data={"token": "abc..."})
    out = _scrub_event(event, None)
    assert "data" not in out["request"]


def test_scrubber_strips_body_on_provider_lookup():
    """The handler decrypts API keys into local scope; a 5xx around the
    upstream call would otherwise carry both the URL and the body."""
    event = _event("POST", "/api/parts/lookup-mpn", data={"mpn": "RC0402JR-070R"})
    out = _scrub_event(event, None)
    assert "data" not in out["request"]


def test_scrubber_strips_body_on_workspaces_patch():
    """The original /api/workspaces case still works — just via the
    method default-deny rather than the URL allow-list."""
    event = _event(
        "PATCH",
        "/api/workspaces/current",
        data={"parts_provider_api_key": "MOUSER-FAKE-KEY"},
    )
    out = _scrub_event(event, None)
    assert "data" not in out["request"]


def test_scrubber_strips_body_on_delete():
    event = _event("DELETE", "/api/parts/abc", data={"reason": "test"})
    out = _scrub_event(event, None)
    assert "data" not in out["request"]


def test_scrubber_strips_body_on_put():
    event = _event("PUT", "/api/whatever", data={"x": 1})
    out = _scrub_event(event, None)
    assert "data" not in out["request"]


# ---------------------------------------------------------------------------
# GET requests keep their data field (Sentry rarely populates it on GET
# but if it does — e.g. a route that mistakenly accepts a body on GET —
# we don't need to touch it; the scrubber's contract is method-based).
# ---------------------------------------------------------------------------


def test_scrubber_keeps_body_on_get():
    event = _event("GET", "/api/parts", data={"q": "search"})
    out = _scrub_event(event, None)
    assert out["request"]["data"] == {"q": "search"}
    assert "body_redacted" not in out["request"]


# ---------------------------------------------------------------------------
# Header scrub — applies on every method.
# ---------------------------------------------------------------------------


def test_scrubber_strips_sensitive_headers_on_get():
    event = _event(
        "GET",
        "/api/parts",
        headers={
            "Cookie": "stockmgr_session=abc",
            "x-workspace-id": "ws-uuid",
            "authorization": "Bearer xyz",
            "X-Trace-Id": "trace-1",
            "user-agent": "Mozilla/5.0",
        },
    )
    out = _scrub_event(event, None)
    h = out["request"]["headers"]
    assert "Cookie" not in h
    assert "x-workspace-id" not in h
    assert "authorization" not in h
    # Non-sensitive headers are kept for triage value.
    assert "X-Trace-Id" in h
    assert "user-agent" in h


def test_scrubber_strips_sensitive_headers_on_post():
    event = _event(
        "POST",
        "/api/parts",
        data={"name": "Resistor"},
        headers={"Cookie": "s=1", "X-Trace-Id": "t"},
    )
    out = _scrub_event(event, None)
    assert "Cookie" not in out["request"]["headers"]
    assert "X-Trace-Id" in out["request"]["headers"]
    assert "data" not in out["request"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_scrubber_handles_event_without_request():
    """Some events (e.g. message capture without an HTTP context) don't
    have a `request` block. Scrubber must no-op rather than raise."""
    out = _scrub_event({"message": "hello"}, None)
    assert out == {"message": "hello"}


def test_strips_exception_value_secrets():
    event = {
        "message": "provider failed api_key=frontend-key token=invite-token",
        "exception": {
            "values": [
                {
                    "type": "RuntimeError",
                    "value": (
                        "lookup failed password=plain-pass "
                        "api_key=provider-key token=raw-token"
                    ),
                    "message": 'request failed with "secret":"json-secret"',
                }
            ]
        },
    }

    out = _scrub_event(event, None)
    serialized = str(out)

    assert "plain-pass" not in serialized
    assert "provider-key" not in serialized
    assert "raw-token" not in serialized
    assert "json-secret" not in serialized
    assert "frontend-key" not in serialized
    assert "invite-token" not in serialized
    assert out["exception"]["values"][0]["value"] == (
        "lookup failed password=[Filtered] api_key=[Filtered] token=[Filtered]"
    )


def test_scrubber_handles_request_without_method():
    """Defensive — older SDK shapes or partial captures might not set
    `method`. We treat that as 'unknown, do nothing destructive'."""
    event = {"request": {"url": "/api/parts", "data": {"x": 1}}}
    out = _scrub_event(event, None)
    # No method → no body strip (method default-deny only fires with a
    # known non-GET).
    assert out["request"]["data"] == {"x": 1}


def test_scrubber_does_not_set_body_redacted_when_no_data():
    """If the request has no body to begin with, we don't add a
    body_redacted flag — that would lie about what was scrubbed."""
    event = _event("POST", "/api/parts/abc/archive")  # no body
    out = _scrub_event(event, None)
    assert "body_redacted" not in out["request"]
