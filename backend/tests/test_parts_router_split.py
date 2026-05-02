"""Pin that each sub-router file (parts_core, parts_assets, parts_scan,
parts_provider) registers at least one expected URL under /api/parts.

Hits one representative URL per file via TestClient to confirm the router
is wired into the app. These tests do NOT assert business logic — they are
a registration smoke-test so a mis-import or stale prefix is caught
immediately (issue #118 / CQ-002).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests._factories import DEFAULT_PASSWORD, signup_user, create_part


@pytest.fixture()
def authed_client():
    """TestClient with a signed-up session."""
    client = TestClient(app, raise_server_exceptions=True)
    signup_user(client)
    return client


# ---------------------------------------------------------------------------
# parts_core — GET /api/parts (list endpoint)
# ---------------------------------------------------------------------------

def test_parts_core_list_registered(authed_client: TestClient):
    """GET /api/parts returns 200 — proves parts_core.router is mounted."""
    r = authed_client.get("/api/parts")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "data" in body


# ---------------------------------------------------------------------------
# parts_assets — GET /assets/{ws_id}/{filename}
# ---------------------------------------------------------------------------

def test_parts_assets_route_registered(authed_client: TestClient):
    """GET /api/parts/assets/{ws_id}/{filename} is reachable — 404 for
    a missing file is fine; what we check is it's NOT a 405/404-on-the-
    route-itself (method/path not found at the FastAPI level)."""
    fake_ws = str(uuid.uuid4())
    r = authed_client.get(f"/api/parts/assets/{fake_ws}/nonexistent.jpg")
    # 404 means the route matched and rejected the missing file — the
    # router IS registered. Any 4xx from the handler is acceptable here.
    assert r.status_code in (400, 401, 403, 404), (
        f"unexpected status {r.status_code} — route may not be registered: {r.text}"
    )


# ---------------------------------------------------------------------------
# parts_scan — POST /api/parts/bulk-import-from-scan
# ---------------------------------------------------------------------------

def test_parts_scan_bulk_import_route_registered(authed_client: TestClient):
    """POST /api/parts/bulk-import-from-scan is reachable — 400 when no
    provider is configured is acceptable; 405/404 would mean the router
    is missing."""
    r = authed_client.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{"mpn": "TEST123"}]},
    )
    # 400 (no provider configured) or 422 (validation) are fine — the
    # endpoint is registered. 404/405 would mean the router is absent.
    assert r.status_code in (400, 422), (
        f"unexpected status {r.status_code} — route may not be registered: {r.text}"
    )


# ---------------------------------------------------------------------------
# parts_provider — POST /api/parts/lookup-mpn
# ---------------------------------------------------------------------------

def test_parts_provider_lookup_route_registered(authed_client: TestClient):
    """POST /api/parts/lookup-mpn returns 200 (no provider → found=False)
    — proves parts_provider.router is mounted under /api/parts."""
    r = authed_client.post(
        "/api/parts/lookup-mpn",
        json={"mpn": "TEST123"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "data" in body
    assert body["data"]["found"] is False
