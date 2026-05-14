"""Tests for the /api/sentry-tunnel hardening (Sec CRIT-5).

The route allows unauthenticated same-origin SDK posts so login-screen
errors can still report. What we pin here:
- 204 short-circuit when no DSN is configured (no upstream egress).
- 400 on empty / malformed / DSN-missing envelopes.
- 403 on a DSN that doesn't match the server's allow-list.
- 413 when the envelope exceeds SENTRY_TUNNEL_MAX_BYTES.
- The route declares the slowapi rate-limit decorator (slowapi is
  disabled in non-prod env, so we don't trip the limit at runtime in
  tests — we verify the wiring instead).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes import sentry_tunnel as sentry_tunnel_route
from app.main import app

_ALLOWED_ENDPOINTS = (("o123.ingest.sentry.io", "456"),)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _allow_test_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sentry_tunnel_route, "ALLOWED_ENDPOINTS", _ALLOWED_ENDPOINTS)


# ---------------------------------------------------------------------------
# 204 short-circuit when no DSN is configured.
# ---------------------------------------------------------------------------


def test_sentry_tunnel_returns_204_when_no_dsn_configured(client, monkeypatch):
    """No DSN on the server → 204 No Content. SDK retries see this as a
    soft acknowledgement and back off."""
    monkeypatch.setattr(sentry_tunnel_route, "ALLOWED_ENDPOINTS", ())

    r = client.post("/api/sentry-tunnel", content=b"anything")

    assert r.status_code == 204, r.text


# ---------------------------------------------------------------------------
# Body-cap (Sec CRIT-5)
# ---------------------------------------------------------------------------


def test_sentry_tunnel_rejects_oversize_envelope(client, monkeypatch):
    """Force a 1 KiB cap, send 2 KiB → 413."""
    from app.core.config import settings

    # A configured DSN is required to reach the size-check path.
    _allow_test_dsn(monkeypatch)
    monkeypatch.setenv("SENTRY_TUNNEL_MAX_BYTES", "1024")
    settings.cache_clear()
    try:
        r = client.post("/api/sentry-tunnel", content=b"X" * 2048)
        assert r.status_code == 413, r.text
    finally:
        settings.cache_clear()


def test_sentry_tunnel_accepts_envelope_at_cap_boundary(client, monkeypatch):
    """A body exactly at the cap should pass the size check (and then
    fail downstream on the malformed envelope header — that's fine,
    we're only verifying the cap is inclusive of the boundary, not
    that arbitrary bytes are valid envelopes)."""
    from app.core.config import settings

    _allow_test_dsn(monkeypatch)
    monkeypatch.setenv("SENTRY_TUNNEL_MAX_BYTES", "1024")
    settings.cache_clear()
    try:
        r = client.post("/api/sentry-tunnel", content=b"X" * 1024)
        # Size check passes (would have been 413 if cap was exclusive);
        # next step is parsing the envelope header which fails as 400.
        assert r.status_code == 400, r.text
        assert "envelope" in (r.json().get("status", {}).get("message") or "").lower()
    finally:
        settings.cache_clear()


# ---------------------------------------------------------------------------
# DSN allow-list — pre-existing behaviour, pinned here to prevent regression
# ---------------------------------------------------------------------------


def test_sentry_tunnel_rejects_empty_envelope(client, monkeypatch):
    _allow_test_dsn(monkeypatch)

    r = client.post("/api/sentry-tunnel", content=b"")

    assert r.status_code == 400, r.text


def test_sentry_tunnel_rejects_malformed_header(client, monkeypatch):
    _allow_test_dsn(monkeypatch)

    r = client.post("/api/sentry-tunnel", content=b"not-json\nrest")

    assert r.status_code == 400, r.text


def test_sentry_tunnel_rejects_dsn_mismatch(client, monkeypatch):
    """Envelope claims a DSN that's not in the server's allow-list →
    403. Without this, the route is an open egress to any Sentry-shaped
    URL the client cares to put in an envelope header."""
    _allow_test_dsn(monkeypatch)

    # Foreign DSN (different host + project_id).
    envelope_header = json.dumps({"dsn": "https://xyz@o999.ingest.sentry.io/000"})
    body = envelope_header.encode() + b"\n{}"
    r = client.post("/api/sentry-tunnel", content=body)

    assert r.status_code == 403, r.text


def test_sentry_tunnel_rejects_envelope_missing_dsn(client, monkeypatch):
    _allow_test_dsn(monkeypatch)

    envelope_header = json.dumps({"event_id": "abc"})  # no `dsn`
    body = envelope_header.encode() + b"\n{}"
    r = client.post("/api/sentry-tunnel", content=body)

    assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# Rate-limit wiring
# ---------------------------------------------------------------------------


def test_sentry_tunnel_route_has_rate_limit_decorator():
    """slowapi's `enabled=False` outside prod means we can't trip the
    limit in tests, but we can verify the decorator was applied — its
    presence is the load-bearing check."""
    import app.api.routes.sentry_tunnel as sentry_tunnel_mod

    sentry_tunnel = sentry_tunnel_mod.sentry_tunnel
    source = Path(sentry_tunnel_mod.__file__).read_text()
    assert '@limiter.limit("30/minute")' in source

    # slowapi attaches a `limiter_kwargs` attribute on the wrapped
    # callable. The exact attribute name is internal; check for any of
    # the public-facing markers slowapi uses across versions.
    markers = ("limiter_kwargs", "_limiter", "limit")
    has_marker = any(hasattr(sentry_tunnel, m) for m in markers)
    # If none of the markers are present, fall back to a less precise
    # but still strong signal: the function's repr should reference
    # slowapi at some level after decoration.
    if not has_marker:
        wrapped = getattr(sentry_tunnel, "__wrapped__", sentry_tunnel)
        assert wrapped is not sentry_tunnel, (
            "expected @limiter.limit to wrap sentry_tunnel — none of "
            f"{markers} found and __wrapped__ unset"
        )
