from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.errors import ErrorCodes
from app.main import app


def _signup(c: TestClient, email: str | None = None) -> str:
    email = email or f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["workspace_id"]


@pytest.fixture
def authed():
    c = TestClient(app)
    _signup(c)
    return c


# ---------------------------------------------------------------------------
# Workspace settings surface — provider config + masked key
# ---------------------------------------------------------------------------

def test_workspace_default_provider_is_none(authed):
    cur = authed.get("/api/workspaces/current").json()["data"]
    assert cur["parts_provider"] == "none"
    assert cur["has_parts_provider_api_key"] is False


def test_workspace_patch_sets_and_masks_api_key(authed):
    r = authed.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": "secret-key-xxx"},
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["parts_provider"] == "mouser"
    assert body["has_parts_provider_api_key"] is True
    # The raw key must never be on the wire.
    assert "parts_provider_api_key" not in body
    assert "secret-key-xxx" not in r.text

    cur = authed.get("/api/workspaces/current").json()["data"]
    assert cur["parts_provider"] == "mouser"
    assert cur["has_parts_provider_api_key"] is True
    assert "secret-key-xxx" not in str(cur)


def test_workspace_patch_empty_string_clears_key(authed):
    authed.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": "k"},
    )
    r = authed.patch(
        "/api/workspaces/current",
        json={"parts_provider_api_key": ""},
    )
    assert r.status_code == 200
    assert r.json()["data"]["has_parts_provider_api_key"] is False


def test_workspace_patch_rejects_unknown_provider(authed):
    r = authed.patch(
        "/api/workspaces/current",
        json={"parts_provider": "octopart"},
    )
    assert r.status_code == 422  # Literal["none","mouser","digikey"] rejects octopart


# ---------------------------------------------------------------------------
# /api/parts/lookup-mpn — provider dispatch
# ---------------------------------------------------------------------------

def _enable_mouser(client: TestClient, key: str = "fake-test-key"):
    r = client.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": key},
    )
    assert r.status_code == 200, r.text


def test_lookup_no_provider_returns_friendly_message(authed):
    r = authed.post("/api/parts/lookup-mpn", json={"mpn": "RC0402JR-070R"})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["found"] is False
    assert "no provider configured" in body["message"]
    assert body["provider"] == "none"


def test_lookup_mouser_success_path(authed, monkeypatch):
    _enable_mouser(authed, "fake-key")

    fake_response = {
        "Errors": [],
        "SearchResults": {
            "NumberOfResult": 1,
            "Parts": [
                {
                    "Manufacturer": "Yageo",
                    "ManufacturerPartNumber": "RC0402JR-070R",
                    "Description": "Thick Film Resistors - SMD 0R 1/16W 5% 0402",
                    "Category": "Resistors",
                    "DataSheetUrl": "https://example.com/datasheet.pdf",
                    "ImagePath": "https://example.com/image.jpg",
                    "ProductDetailUrl": "https://www.mouser.com/ProductDetail/...",
                    "ProductAttributes": [
                        {"AttributeName": "Resistance", "AttributeValue": "0 Ohms"},
                        {"AttributeName": "Tolerance", "AttributeValue": "5 %"},
                        {"AttributeName": "Power Rating", "AttributeValue": "1/16 W"},
                        {"AttributeName": "Package / Case", "AttributeValue": "0402 (1005 Metric)"},
                        # malformed rows should be skipped
                        {"AttributeName": "", "AttributeValue": "skip"},
                        {"AttributeName": "Empty", "AttributeValue": ""},
                    ],
                }
            ],
        },
    }

    captured = {}

    def fake_post(url, payload):
        captured["url"] = url
        captured["payload"] = payload
        return fake_response

    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser", fake_post
    )

    r = authed.post("/api/parts/lookup-mpn", json={"mpn": "RC0402JR-070R"})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["found"] is True
    assert body["provider"] == "mouser"
    assert body["result"]["mpn"] == "RC0402JR-070R"
    assert body["result"]["manufacturer"] == "Yageo"
    assert body["result"]["datasheet_url"] == "https://example.com/datasheet.pdf"
    assert body["result"]["image_url"] == "https://example.com/image.jpg"
    assert body["result"]["category"] == "Resistors"
    # ProductAttributes flow through as specs[]; malformed rows are dropped.
    spec_keys = [s["key"] for s in body["result"]["specs"]]
    assert "Resistance" in spec_keys
    assert "Tolerance" in spec_keys
    assert "Power Rating" in spec_keys
    assert "Package / Case" in spec_keys
    assert "" not in spec_keys
    assert "Empty" not in spec_keys
    # Footprint is auto-extracted from the matching attribute name.
    assert body["result"]["footprint"] == "0402 (1005 Metric)"
    # API key must travel in the URL we POST to, not anywhere else
    assert "fake-key" in captured["url"]
    assert captured["payload"]["SearchByPartRequest"]["mouserPartNumber"] == "RC0402JR-070R"
    # We deliberately don't pass partSearchOptions: "Exact" because it
    # only matches Mouser's own part numbers, not manufacturer MPNs.
    assert "partSearchOptions" not in captured["payload"]["SearchByPartRequest"]


def test_lookup_mouser_no_match(authed, monkeypatch):
    _enable_mouser(authed)

    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: {"Errors": [], "SearchResults": {"NumberOfResult": 0, "Parts": []}},
    )
    r = authed.post("/api/parts/lookup-mpn", json={"mpn": "DOES-NOT-EXIST"})
    body = r.json()["data"]
    assert body["found"] is False
    assert "no match" in body["message"].lower()
    assert body["provider"] == "mouser"


def test_lookup_mouser_returns_errors(authed, monkeypatch):
    _enable_mouser(authed, "bad-key")
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: {
            "Errors": [{"Id": 0, "Code": "Invalid", "Message": "Invalid Unique Identifier"}],
            "SearchResults": None,
        },
    )
    r = authed.post("/api/parts/lookup-mpn", json={"mpn": "ANY"})
    body = r.json()["data"]
    assert body["found"] is False
    assert body["message"] == "Invalid Unique Identifier"


def test_lookup_mouser_translates_invalid_api_key(authed, monkeypatch):
    """Mouser surfaces a rejected API key as `Invalid unique identifier.`
    with PropertyName='API Key' — useless to the operator. The provider
    should translate that into a clear, actionable message naming the
    setting to fix."""
    _enable_mouser(authed, "ZnGz3970R7NTBfR1gXxUGlMN4xDNTIn4xM68chzUXzdP9sjw")
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: {
            "Errors": [{
                "Id": 0,
                "Code": "Invalid",
                "Message": "Invalid unique identifier.",
                "ResourceKey": "InvalidIdentifier",
                "PropertyName": "API Key",
            }],
            "SearchResults": None,
        },
    )
    r = authed.post("/api/parts/lookup-mpn", json={"mpn": "ANY"})
    body = r.json()["data"]
    assert body["found"] is False
    msg = body["message"].lower()
    assert "api key" in msg
    assert "settings" in msg or "workspace" in msg
    # Don't pass through the raw "Invalid unique identifier" — that's the
    # confusing original message the translation is replacing.
    assert "invalid unique identifier" not in msg


def test_lookup_mouser_network_failure_is_graceful(authed, monkeypatch):
    _enable_mouser(authed)

    def fail(url, payload):
        raise httpx.ConnectError("simulated connection failure")

    monkeypatch.setattr("app.domain.parts.providers.mouser._post_mouser", fail)
    r = authed.post("/api/parts/lookup-mpn", json={"mpn": "ANY"})
    assert r.status_code == 502, r.text
    body = r.json()
    assert body["data"] is None
    assert body["code"] == ErrorCodes.PROVIDER_UPSTREAM_ERROR
    assert "upstream unavailable" in body["status"]["message"]
    assert "connection failed" in body["status"]["message"]
    assert body["provider"] == "mouser"


def test_lookup_rejects_extra_fields(authed):
    r = authed.post("/api/parts/lookup-mpn", json={"mpn": "X", "supplier": "y"})
    assert r.status_code == 422


def test_lookup_requires_member_role(authed):
    """A viewer is blocked by the router-level require_member_for_writes
    on POST /api/parts/lookup-mpn."""
    invitee_email = f"viewer-{uuid.uuid4().hex[:6]}@x.com"
    inv = authed.post(
        "/api/invitations", json={"email": invitee_email, "role": "viewer"}
    ).json()["data"]
    token = inv["token"]

    viewer = TestClient(app)
    viewer.post(
        "/api/auth/signup",
        json={"email": invitee_email, "name": "Vee", "password": "TestPass-2026-Stronk"},
    )
    viewer.post("/api/invitations/accept", json={"token": token})
    # Switch viewer to the shared workspace
    me = viewer.get("/api/auth/me").json()["data"]
    viewer_personal = me["workspaces"][0]["id"]
    wss = viewer.get("/api/workspaces").json()["data"]
    shared = next(w for w in wss if w["id"] != viewer_personal)
    viewer.post(f"/api/workspaces/{shared['id']}/switch")

    r = viewer.post("/api/parts/lookup-mpn", json={"mpn": "ANY"})
    assert r.status_code == 403, r.text
