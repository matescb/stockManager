from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.routes import trustedparts
from app.main import app


def _signup(c: TestClient, email: str | None = None) -> str:
    email = email or f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": "password123"},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["workspace_id"]


@pytest.fixture
def authed():
    c = TestClient(app)
    _signup(c)
    return c


_FAKE_PRODUCT_HTML = """
<!DOCTYPE html>
<html><head>
<title>RC0402JR-070R - Yageo - Trusted Parts</title>
<meta property="og:description" content="Resistor 0 Ohm, 0402, jumper" />
<meta property="product:brand" content="Yageo" />
<meta property="product:category" content="Resistors" />
</head><body>
<dl>
  <dt>Manufacturer</dt><dd>Yageo</dd>
  <dt>Package / Case</dt><dd>0402 (1005 Metric)</dd>
  <dt>Description</dt><dd>Chip resistor, 0R, 1/16W, 0402</dd>
</dl>
<a href="/datasheets/yageo-rc0402.pdf">Datasheet</a>
</body></html>
"""


def test_lookup_success_path(authed, monkeypatch):
    """Success path: monkeypatch the network seam to return a canned
    product page and verify the route extracts manufacturer / description
    / footprint / datasheet_url."""
    final_url = "https://www.trustedparts.com/en/part/yageo/RC0402JR-070R"

    def fake_fetch(url: str):
        assert "RC0402JR-070R" in url
        return final_url, _FAKE_PRODUCT_HTML

    monkeypatch.setattr(trustedparts, "_fetch_html", fake_fetch)

    r = authed.post("/api/trustedparts/lookup", json={"mpn": "RC0402JR-070R"})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["found"] is True
    res = body["result"]
    assert res["mpn"] == "RC0402JR-070R"
    assert res["manufacturer"] == "Yageo"
    assert res["description"] == "Resistor 0 Ohm, 0402, jumper"
    assert res["footprint"] == "0402 (1005 Metric)"
    assert res["category"] == "Resistors"
    assert res["datasheet_url"].endswith("yageo-rc0402.pdf")
    assert res["datasheet_url"].startswith("https://")
    assert res["source_url"] == final_url


def test_lookup_failure_returns_200(authed, monkeypatch):
    """Network failure must surface as 200 + found=false, never a 500.
    The frontend renders a small inline note."""

    def boom(_url: str):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(trustedparts, "_fetch_html", boom)

    r = authed.post("/api/trustedparts/lookup", json={"mpn": "RC0402JR-070R"})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["found"] is False
    assert body["result"] is None
    assert isinstance(body["message"], str) and "upstream" in body["message"]


def test_lookup_no_match_returns_found_false(authed, monkeypatch):
    """If the upstream HTML doesn't look like a product page (no brand,
    description, or datasheet), we report no-match instead of returning
    a half-empty record."""

    def fake_fetch(_url: str):
        return "https://www.trustedparts.com/en/search/zzzzz", "<html><body>no results</body></html>"

    monkeypatch.setattr(trustedparts, "_fetch_html", fake_fetch)

    r = authed.post("/api/trustedparts/lookup", json={"mpn": "zzzzz"})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["found"] is False
    assert body["result"] is None


def test_viewer_cannot_lookup(monkeypatch):
    """Lookup is a write-shaped action (POST). Viewers must be blocked
    by the router-level require_member_for_writes gate, regardless of
    whether the upstream call would have succeeded."""
    # Prevent any network even if the gate were misconfigured.
    monkeypatch.setattr(
        trustedparts,
        "_fetch_html",
        lambda _u: ("https://example.com", _FAKE_PRODUCT_HTML),
    )

    owner = TestClient(app)
    _signup(owner)

    invitee_email = f"viewer-{uuid.uuid4().hex[:6]}@x.com"
    inv = owner.post(
        "/api/invitations",
        json={"email": invitee_email, "role": "viewer"},
    ).json()["data"]
    token = inv["token"]

    viewer = TestClient(app)
    viewer.post(
        "/api/auth/signup",
        json={"email": invitee_email, "name": "Vee", "password": "password123"},
    )
    viewer.post("/api/invitations/accept", json={"token": token})

    me = viewer.get("/api/auth/me").json()["data"]
    viewer_personal = me["workspaces"][0]["id"]
    wss = viewer.get("/api/workspaces").json()["data"]
    shared = next(w for w in wss if w["id"] != viewer_personal)
    viewer.post(f"/api/workspaces/{shared['id']}/switch")

    r = viewer.post("/api/trustedparts/lookup", json={"mpn": "RC0402JR-070R"})
    assert r.status_code == 403, r.text


def test_lookup_rejects_extra_fields(authed):
    """Pydantic extra='forbid' contract — be consistent with the rest of
    the API."""
    r = authed.post("/api/trustedparts/lookup", json={"mpn": "X", "evil": True})
    assert r.status_code == 422
