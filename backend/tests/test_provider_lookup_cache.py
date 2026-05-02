"""Tests for BE2-011: provider result caching in lookup_with_cache.

The cache ensures that identical (provider, mpn) queries within the TTL
window hit the upstream exactly once, saving API quota and reducing latency.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _signup(email: str | None = None) -> tuple[TestClient, str]:
    c = TestClient(app)
    email = email or f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text
    ws_id = r.json()["data"]["workspace_id"]
    return c, ws_id


def _enable_mouser(client: TestClient, key: str = "fake-test-key") -> None:
    r = client.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": key},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Cache hit test — provider called only once for identical MPN
# ---------------------------------------------------------------------------


def test_cache_deduplicates_identical_mpn_lookups(monkeypatch):
    """Two identical MPN lookups within the TTL should call the upstream
    exactly once; the second call is served from the in-process cache."""
    import app.domain.parts.services.provider_cache as _cache_mod

    # Reset module-level cache singleton for isolation.
    _cache_mod._cache._store.clear()

    call_count = 0
    found_response = {
        "Errors": [],
        "SearchResults": {
            "NumberOfResult": 1,
            "Parts": [
                {
                    "ManufacturerPartNumber": "GRM188R61A106KE69D",
                    "Manufacturer": "Murata",
                    "Description": "MLCC 10uF 10V 0603",
                    "Category": "Capacitors",
                    "ProductAttributes": [],
                    "PriceBreaks": [],
                    "DataSheetUrl": "",
                    "ImagePath": "",
                    "ProductDetailUrl": "",
                    "MouserPartNumber": "81-GRM188R61A106KE69D",
                    "LifecycleStatus": "Active",
                    "ROHSStatus": "RoHS Compliant",
                    "Availability": "1000 In Stock",
                    "LeadTime": "",
                    "ProductCompliance": [],
                }
            ],
        },
    }

    def _fake_post_mouser(url: str, payload: dict) -> dict:
        nonlocal call_count
        call_count += 1
        return found_response

    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        _fake_post_mouser,
    )

    c, _ = _signup()
    _enable_mouser(c)

    mpn = f"GRM188R61A106KE69D-{uuid.uuid4().hex[:4]}"

    r1 = c.post("/api/parts/lookup-mpn", json={"mpn": mpn})
    assert r1.status_code == 200, r1.text

    r2 = c.post("/api/parts/lookup-mpn", json={"mpn": mpn})
    assert r2.status_code == 200, r2.text

    # The underlying provider function must have been called exactly once.
    assert call_count == 1, (
        f"Expected upstream to be called once (cache should serve the second "
        f"request), but it was called {call_count} times."
    )

    # Both responses should be identical found=True results.
    assert r1.json()["data"]["found"] is True
    assert r2.json()["data"]["found"] is True


# ---------------------------------------------------------------------------
# Cache miss for negative results uses a shorter TTL but still deduplicates
# ---------------------------------------------------------------------------


def test_cache_deduplicates_negative_miss(monkeypatch):
    """Two 'no match' lookups for the same MPN should call the upstream
    exactly once within the miss TTL window."""
    import app.domain.parts.services.provider_cache as _cache_mod

    _cache_mod._cache._store.clear()

    call_count = 0
    no_match_response = {
        "Errors": [],
        "SearchResults": {"NumberOfResult": 0, "Parts": []},
    }

    def _fake_post_mouser(url: str, payload: dict) -> dict:
        nonlocal call_count
        call_count += 1
        return no_match_response

    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        _fake_post_mouser,
    )

    c, _ = _signup()
    _enable_mouser(c)

    mpn = f"NONEXISTENT-MPN-{uuid.uuid4().hex[:4]}"

    r1 = c.post("/api/parts/lookup-mpn", json={"mpn": mpn})
    assert r1.status_code == 200
    assert r1.json()["data"]["found"] is False

    r2 = c.post("/api/parts/lookup-mpn", json={"mpn": mpn})
    assert r2.status_code == 200
    assert r2.json()["data"]["found"] is False

    assert call_count == 1, (
        f"Expected upstream called once for negative result, got {call_count}."
    )


# ---------------------------------------------------------------------------
# Cache is keyed on normalised MPN (strip + lower)
# ---------------------------------------------------------------------------


def test_cache_key_normalises_mpn_case_and_whitespace(monkeypatch):
    """'  GRM188  ' and 'grm188' should hit the same cache entry."""
    import app.domain.parts.services.provider_cache as _cache_mod

    _cache_mod._cache._store.clear()

    call_count = 0
    unique_mpn = f"NRM-{uuid.uuid4().hex[:6]}"

    found_response = {
        "Errors": [],
        "SearchResults": {
            "NumberOfResult": 1,
            "Parts": [
                {
                    "ManufacturerPartNumber": unique_mpn,
                    "Manufacturer": "Test",
                    "Description": "Test part",
                    "Category": "Test",
                    "ProductAttributes": [],
                    "PriceBreaks": [],
                    "DataSheetUrl": "",
                    "ImagePath": "",
                    "ProductDetailUrl": "",
                    "MouserPartNumber": f"TEST-{unique_mpn}",
                    "LifecycleStatus": "Active",
                    "ROHSStatus": "",
                    "Availability": "100 In Stock",
                    "LeadTime": "",
                    "ProductCompliance": [],
                }
            ],
        },
    }

    def _fake_post_mouser(url: str, payload: dict) -> dict:
        nonlocal call_count
        call_count += 1
        return found_response

    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        _fake_post_mouser,
    )

    c, _ = _signup()
    _enable_mouser(c)

    r1 = c.post("/api/parts/lookup-mpn", json={"mpn": unique_mpn})
    assert r1.status_code == 200
    r2 = c.post("/api/parts/lookup-mpn", json={"mpn": f"  {unique_mpn.lower()}  "})
    assert r2.status_code == 200

    assert call_count == 1, (
        f"Same MPN with different case/whitespace should resolve to one cache entry, "
        f"but upstream was called {call_count} times."
    )
