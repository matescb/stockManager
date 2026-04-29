from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient) -> str:
    r = c.post(
        "/api/auth/signup",
        json={"email": f"u-{uuid.uuid4().hex[:8]}@x.com", "name": "u", "password": "password123"},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["workspace_id"]


@pytest.fixture
def authed():
    c = TestClient(app)
    _signup(c)
    return c


# ---------------------------------------------------------------------------
# Scanner backend — defaults + patch surface
# ---------------------------------------------------------------------------


def test_workspace_default_scanner_is_zxing(authed):
    """New workspaces ship with the open-source decoder so users without a
    Scandit license can scan immediately."""
    cur = authed.get("/api/workspaces/current").json()["data"]
    assert cur["scanner"] == "zxing"
    assert cur["has_scanner_license_key"] is False


def test_workspace_patch_sets_and_masks_scanner_license_key(authed):
    r = authed.patch(
        "/api/workspaces/current",
        json={"scanner": "scandit", "scanner_license_key": "AR-secret-license-key"},
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["scanner"] == "scandit"
    assert body["has_scanner_license_key"] is True
    # The raw key must never be on the wire.
    assert "scanner_license_key" not in body
    assert "AR-secret-license-key" not in r.text

    cur = authed.get("/api/workspaces/current").json()["data"]
    assert cur["scanner"] == "scandit"
    assert cur["has_scanner_license_key"] is True
    assert "AR-secret-license-key" not in str(cur)


def test_workspace_patch_empty_string_clears_scanner_license_key(authed):
    authed.patch(
        "/api/workspaces/current",
        json={"scanner": "scandit", "scanner_license_key": "k"},
    )
    r = authed.patch(
        "/api/workspaces/current",
        json={"scanner_license_key": ""},
    )
    assert r.status_code == 200
    assert r.json()["data"]["has_scanner_license_key"] is False


def test_workspace_patch_rejects_unknown_scanner(authed):
    r = authed.patch(
        "/api/workspaces/current",
        json={"scanner": "zbar"},
    )
    assert r.status_code == 422  # Literal["zxing","scandit"] rejects zbar


def test_switching_scanner_does_not_clear_stored_license_key(authed):
    """Toggling 'scandit' → 'zxing' → 'scandit' should keep whatever key the
    user already pasted, so they don't have to re-enter it on each switch."""
    authed.patch(
        "/api/workspaces/current",
        json={"scanner": "scandit", "scanner_license_key": "AR-keep-me"},
    )
    authed.patch("/api/workspaces/current", json={"scanner": "zxing"})
    cur = authed.get("/api/workspaces/current").json()["data"]
    assert cur["scanner"] == "zxing"
    # Key is preserved across the toggle — the operator just isn't using it.
    assert cur["has_scanner_license_key"] is True

    authed.patch("/api/workspaces/current", json={"scanner": "scandit"})
    cur = authed.get("/api/workspaces/current").json()["data"]
    assert cur["scanner"] == "scandit"
    assert cur["has_scanner_license_key"] is True
