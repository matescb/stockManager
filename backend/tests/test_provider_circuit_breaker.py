"""Tests for BE2-011: circuit breaker in provider_cache.lookup_with_cache.

After N consecutive hard failures the breaker opens and subsequent calls
return a synthetic "temporarily unavailable" response without hitting the
upstream.  The counter resets on a successful call.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Unit tests against lookup_with_cache directly (no HTTP layer needed)
# ---------------------------------------------------------------------------


def _make_provider(name: str = "test-provider"):
    """Return a minimal object that satisfies the PartsProvider protocol."""

    class _FakeProvider:
        pass

    p = _FakeProvider()
    p.name = name  # type: ignore[attr-defined]
    return p


def _reset_state(provider_name: str) -> None:
    """Clear cache entry and reset circuit breaker for isolation."""
    import app.domain.parts.services.provider_cache as _m

    # Clear the TTL cache.
    _m._cache._store.clear()

    # Remove the breaker entry so each test starts fresh.
    _m._breakers.pop(provider_name, None)


# ---------------------------------------------------------------------------
# Breaker opens after threshold consecutive failures
# ---------------------------------------------------------------------------


def test_breaker_opens_after_threshold_failures(monkeypatch):
    """After _CB_FAIL_THRESHOLD hard failures the breaker opens; the next
    call returns the synthetic unavailable message without hitting the
    provider."""
    import app.domain.parts.services.provider_cache as _m

    provider_name = "breaker-test-open"
    _reset_state(provider_name)

    provider = _make_provider(provider_name)
    call_count = 0

    def _fail_lookup(mpn: str) -> dict:
        nonlocal call_count
        call_count += 1
        return {"found": False, "result": None, "message": "upstream unavailable (ConnectionError)"}

    provider.lookup_mpn = _fail_lookup  # type: ignore[attr-defined]

    threshold = _m._CB_FAIL_THRESHOLD

    # Fire exactly threshold calls — all should reach the provider.
    for i in range(threshold):
        result = _m.lookup_with_cache(provider, f"MPN-{i}")
        assert result["found"] is False

    assert call_count == threshold, (
        f"Expected exactly {threshold} upstream calls before breaker opened, "
        f"got {call_count}."
    )

    # The (threshold + 1)-th call should be intercepted by the open breaker.
    result = _m.lookup_with_cache(provider, "MPN-EXTRA")
    assert result["found"] is False
    assert "circuit breaker" in (result.get("message") or "").lower(), (
        f"Expected circuit-breaker message, got: {result.get('message')!r}"
    )
    # Provider must NOT have been called again.
    assert call_count == threshold, (
        f"Provider should not be called when breaker is open, "
        f"but call_count is {call_count}."
    )


# ---------------------------------------------------------------------------
# Breaker counter resets on success
# ---------------------------------------------------------------------------


def test_breaker_counter_resets_on_success(monkeypatch):
    """A successful lookup resets the consecutive-failure counter so the
    threshold must be reached again from zero before the breaker reopens."""
    import app.domain.parts.services.provider_cache as _m

    provider_name = "breaker-test-reset"
    _reset_state(provider_name)

    provider = _make_provider(provider_name)
    threshold = _m._CB_FAIL_THRESHOLD
    fail_count = [0]
    should_fail = [True]

    def _toggle_lookup(mpn: str) -> dict:
        if should_fail[0]:
            fail_count[0] += 1
            return {
                "found": False,
                "result": None,
                "message": "upstream unavailable (TimeoutError)",
            }
        return {"found": True, "result": {"mpn": mpn}, "message": None}

    provider.lookup_mpn = _toggle_lookup  # type: ignore[attr-defined]

    # Accumulate threshold - 1 failures (breaker not yet open).
    for i in range(threshold - 1):
        _m.lookup_with_cache(provider, f"FAIL-{i}")

    # Now succeed — counter should reset.
    should_fail[0] = False
    result = _m.lookup_with_cache(provider, "SUCCESS-MPN")
    assert result["found"] is True

    # Now fail again; we need a full threshold of NEW failures to open.
    should_fail[0] = True
    breaker = _m._get_breaker(provider_name)
    # After reset the counter should be 0.
    assert breaker._consecutive_failures == 0, (
        f"Expected failure counter to reset to 0 after success, "
        f"got {breaker._consecutive_failures}."
    )


# ---------------------------------------------------------------------------
# "No match" results do NOT trip the breaker
# ---------------------------------------------------------------------------


def test_clean_miss_does_not_trip_breaker(monkeypatch):
    """A 'no match for MPN' response is not a hard failure — calling the
    endpoint many times for non-existent MPNs must not open the breaker."""
    import app.domain.parts.services.provider_cache as _m

    provider_name = "breaker-test-miss"
    _reset_state(provider_name)

    provider = _make_provider(provider_name)

    def _miss_lookup(mpn: str) -> dict:
        return {"found": False, "result": None, "message": "no match for MPN"}

    provider.lookup_mpn = _miss_lookup  # type: ignore[attr-defined]

    threshold = _m._CB_FAIL_THRESHOLD

    # Fire many more than threshold calls; each is a "clean" miss.
    for i in range(threshold + 5):
        result = _m.lookup_with_cache(provider, f"NONEXISTENT-{i}")
        assert result["found"] is False

    breaker = _m._get_breaker(provider_name)
    assert not breaker.is_open, (
        "Breaker must not open on clean 'no match' misses."
    )


# ---------------------------------------------------------------------------
# Integration: circuit breaker via HTTP route
# ---------------------------------------------------------------------------


def test_circuit_breaker_returns_503_like_message_via_route(monkeypatch):
    """Exhausting the circuit breaker via the HTTP route returns a found=False
    result with the unavailability message without a network round-trip."""
    import uuid
    import app.domain.parts.services.provider_cache as _m
    from fastapi.testclient import TestClient
    from app.main import app

    provider_name = "mouser"
    _reset_state(provider_name)

    threshold = _m._CB_FAIL_THRESHOLD
    call_count = [0]

    def _fail_post_mouser(url: str, payload: dict) -> dict:
        call_count[0] += 1
        raise RuntimeError("simulated upstream failure")

    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        _fail_post_mouser,
    )

    c = TestClient(app)
    email = f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text

    r = c.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": "fake-key"},
    )
    assert r.status_code == 200, r.text

    # Trip the breaker with threshold calls using distinct MPNs (so no
    # cache hit deduplicates them).
    for i in range(threshold):
        mpn = f"TRIP-{uuid.uuid4().hex[:6]}"
        r = c.post("/api/parts/lookup-mpn", json={"mpn": mpn})
        assert r.status_code == 200, r.text

    assert call_count[0] == threshold

    # The breaker is now open — next call must not hit the network.
    mpn_after = f"AFTER-{uuid.uuid4().hex[:6]}"
    r = c.post("/api/parts/lookup-mpn", json={"mpn": mpn_after})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["found"] is False
    assert "circuit breaker" in (data.get("message") or "").lower(), (
        f"Expected circuit-breaker message in response, got: {data}"
    )
    # Provider not called again.
    assert call_count[0] == threshold, (
        f"Provider must not be called when breaker is open, "
        f"but was called {call_count[0]} times total."
    )
