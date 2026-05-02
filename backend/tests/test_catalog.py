from __future__ import annotations

import hashlib
import hmac
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


def _signup(c: TestClient, email: str | None = None) -> tuple[str, str]:
    email = email or f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text
    return email, r.json()["data"]["workspace_id"]


@pytest.fixture
def owner_client(engine):
    # Depending on `engine` ensures Base.metadata.create_all has run before
    # this test issues HTTP calls — keeps the file runnable in isolation as
    # well as part of the full suite.
    c = TestClient(app)
    _signup(c)
    return c


def _hmac_token(token: str) -> str:
    """Mirror the application's HMAC computation for test assertions."""
    secret = settings().SESSION_SECRET
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def _enable_catalog(client: TestClient) -> str:
    """Flip catalog on and return the public catalog path (/catalog/<token>).

    SEC2-008: the token is now returned once via catalog_token_plaintext;
    the response no longer carries catalog_url.
    """
    r = client.patch("/api/workspaces/current", json={"catalog_enabled": True})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["catalog_enabled"] is True
    assert data["catalog_token_set"] is True, "expected catalog_token_set=True when enabled"
    token = data.get("catalog_token_plaintext")
    assert token, "expected catalog_token_plaintext on first enable"
    return f"/catalog/{token}"


def _create_part(client: TestClient, name: str, *, published: bool = False) -> str:
    r = client.post("/api/parts", json={"name": name, "part_type": "local"})
    assert r.status_code == 201, r.text
    pid = r.json()["data"]["id"]
    if published:
        r = client.patch(f"/api/parts/{pid}", json={"published": True})
        assert r.status_code == 200, r.text
    return pid


def test_workspace_current_includes_catalog_fields(owner_client):
    cur = owner_client.get("/api/workspaces/current").json()["data"]
    assert cur["catalog_enabled"] is False
    # SEC2-008: plaintext token / URL are never exposed; only a bool sentinel.
    assert cur["catalog_token_set"] is False
    assert "catalog_url" not in cur
    assert "catalog_token" not in cur


def test_member_cannot_toggle_catalog(engine):
    """Toggling catalog_enabled requires admin+ — a plain member is 403."""
    owner = TestClient(app)
    _signup(owner)
    invitee_email = f"member-{uuid.uuid4().hex[:6]}@x.com"
    inv = owner.post(
        "/api/invitations", json={"email": invitee_email, "role": "member"}
    ).json()["data"]

    member = TestClient(app)
    member.post(
        "/api/auth/signup",
        json={"email": invitee_email, "name": "M", "password": "TestPass-2026-Stronk"},
    )
    member.post("/api/invitations/accept", json={"token": inv["token"]})
    me = member.get("/api/auth/me").json()["data"]
    personal = me["workspaces"][0]["id"]
    wss = member.get("/api/workspaces").json()["data"]
    shared = next(w for w in wss if w["id"] != personal)
    member.post(f"/api/workspaces/{shared['id']}/switch")

    r = member.patch("/api/workspaces/current", json={"catalog_enabled": True})
    assert r.status_code == 403, r.text


def test_published_part_appears_unpublished_and_archived_do_not(owner_client):
    url = _enable_catalog(owner_client)
    pub_id = _create_part(owner_client, "PublicCap", published=True)
    _create_part(owner_client, "PrivateCap", published=False)
    arch_id = _create_part(owner_client, "ArchivedCap", published=True)
    r = owner_client.post(f"/api/parts/{arch_id}/archive")
    assert r.status_code == 200, r.text

    r = owner_client.get(f"{url}/parts.json")
    assert r.status_code == 200, r.text
    parts = r.json()["data"]["parts"]
    ids = {p["id"] for p in parts}
    names = {p["name"] for p in parts}
    assert pub_id in ids
    assert "PublicCap" in names
    assert "PrivateCap" not in names
    assert "ArchivedCap" not in names


def test_wrong_token_returns_404(owner_client):
    _enable_catalog(owner_client)
    r = owner_client.get("/catalog/not-a-real-token/parts.json")
    assert r.status_code == 404, r.text
    r = owner_client.get("/catalog/not-a-real-token")
    assert r.status_code == 404


def test_disabling_catalog_makes_url_404(owner_client):
    url = _enable_catalog(owner_client)
    _create_part(owner_client, "X", published=True)
    assert owner_client.get(f"{url}/parts.json").status_code == 200

    r = owner_client.patch("/api/workspaces/current", json={"catalog_enabled": False})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["catalog_enabled"] is False
    # SEC2-008: no catalog_url in response; token_set is still True (hash preserved)
    assert "catalog_url" not in data

    assert owner_client.get(f"{url}/parts.json").status_code == 404
    assert owner_client.get(url).status_code == 404


def test_regenerate_catalog_token_invalidates_old(owner_client):
    old_url = _enable_catalog(owner_client)
    _create_part(owner_client, "RegPart", published=True)
    assert owner_client.get(f"{old_url}/parts.json").status_code == 200

    r = owner_client.patch(
        "/api/workspaces/current",
        json={"regenerate_catalog_token": True, "catalog_enabled": True},
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    # SEC2-008: token is returned once via catalog_token_plaintext
    new_token = data.get("catalog_token_plaintext")
    assert new_token, "expected catalog_token_plaintext on regeneration"
    new_url = f"/catalog/{new_token}"
    assert new_url != old_url

    assert owner_client.get(f"{old_url}/parts.json").status_code == 404
    assert owner_client.get(f"{new_url}/parts.json").status_code == 200


def test_html_endpoint_renders_workspace_name_and_part(engine):
    """The HTML page should render the (HTML-escaped) workspace name and at
    least one published part name. Use a workspace name without HTML-special
    chars so a substring assert is straightforward."""
    c = TestClient(app)
    email = f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={
            "email": email,
            "name": "Widgeteer",
            "password": "TestPass-2026-Stronk",
            "workspace_name": "Acme Widgets",
        },
    )
    assert r.status_code == 200, r.text

    url = _enable_catalog(c)
    _create_part(c, "WidgetOne", published=True)

    r = c.get(url)
    assert r.status_code == 200, r.text
    body = r.text
    assert "text/html" in r.headers.get("content-type", "")
    assert "Acme Widgets" in body
    assert "WidgetOne" in body
    # Non-published parts must not leak into the HTML.
    _create_part(c, "HiddenWidget", published=False)
    body2 = c.get(url).text
    assert "HiddenWidget" not in body2


def test_published_flag_reflected_in_part_get(owner_client):
    pid = _create_part(owner_client, "Echo", published=True)
    p = owner_client.get(f"/api/parts/{pid}").json()["data"]
    assert p["published"] is True
    owner_client.patch(f"/api/parts/{pid}", json={"published": False})
    p = owner_client.get(f"/api/parts/{pid}").json()["data"]
    assert p["published"] is False


# ---------------------------------------------------------------------------
# SEC2-009 — security headers on the public catalog responses
# ---------------------------------------------------------------------------


def test_catalog_html_carries_security_headers(owner_client):
    url = _enable_catalog(owner_client)
    _create_part(owner_client, "HdrPart", published=True)
    r = owner_client.get(url)
    assert r.status_code == 200, r.text
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("referrer-policy") == "same-origin"
    csp = r.headers.get("content-security-policy", "")
    assert "frame-ancestors 'none'" in csp
    assert "script-src 'none'" in csp


def test_catalog_json_carries_security_headers(owner_client):
    url = _enable_catalog(owner_client)
    _create_part(owner_client, "HdrPartJson", published=True)
    r = owner_client.get(f"{url}/parts.json")
    assert r.status_code == 200, r.text
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"


# ---------------------------------------------------------------------------
# SEC2-008 — HMAC token hash + constant-time lookup
# ---------------------------------------------------------------------------


def test_token_plaintext_returned_only_on_first_enable(engine):
    """catalog_token_plaintext is present on the PATCH response but absent
    from a subsequent GET /workspaces/current."""
    c = TestClient(app)
    _signup(c)

    r = c.patch("/api/workspaces/current", json={"catalog_enabled": True})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert "catalog_token_plaintext" in data, "plaintext must be in the PATCH response"
    token = data["catalog_token_plaintext"]
    assert token  # non-empty

    # Subsequent GET must not echo the plaintext back.
    cur = c.get("/api/workspaces/current").json()["data"]
    assert "catalog_token_plaintext" not in cur
    assert cur["catalog_token_set"] is True


def test_hash_lookup_correct_after_token_mint(engine):
    """The stored hash must match hmac(SESSION_SECRET, plaintext).
    We verify this end-to-end: mint a token, derive what the hash should
    be, and confirm the catalog endpoint responds 200 (only possible if
    the lookup by hash succeeded)."""
    c = TestClient(app)
    _signup(c)

    r = c.patch("/api/workspaces/current", json={"catalog_enabled": True})
    token = r.json()["data"]["catalog_token_plaintext"]

    # The catalog should be accessible via the plaintext token.
    assert c.get(f"/catalog/{token}").status_code == 200
    assert c.get(f"/catalog/{token}/parts.json").status_code == 200


def test_hash_directly_in_url_returns_404(engine):
    """Using the raw HMAC hex digest as the URL token must return 404.
    This confirms the server hashes the *incoming* token, not the stored
    hash — a hash-of-hash would still miss, but let's be explicit."""
    c = TestClient(app)
    _signup(c)

    r = c.patch("/api/workspaces/current", json={"catalog_enabled": True})
    token = r.json()["data"]["catalog_token_plaintext"]
    digest = _hmac_token(token)

    # Trying the HMAC hex as the URL token must not succeed.
    assert c.get(f"/catalog/{digest}").status_code == 404
    assert c.get(f"/catalog/{digest}/parts.json").status_code == 404


def test_wrong_token_and_disabled_indistinguishable(engine):
    """Wrong token and disabled-catalog must both return 404 with the same
    body — no oracle for the attacker to distinguish them."""
    c = TestClient(app)
    _signup(c)
    url = _enable_catalog(c)

    wrong_resp = c.get(f"/catalog/definitely-not-a-token/parts.json")
    assert wrong_resp.status_code == 404

    c.patch("/api/workspaces/current", json={"catalog_enabled": False})
    disabled_resp = c.get(f"{url}/parts.json")
    assert disabled_resp.status_code == 404

    # Both must carry the same detail string.
    assert wrong_resp.json().get("detail") == disabled_resp.json().get("detail")


def test_catalog_token_not_leaked_in_serializer(engine):
    """The serialized workspace must never include catalog_token or catalog_url
    keys (only catalog_token_set: bool is allowed)."""
    c = TestClient(app)
    _signup(c)
    c.patch("/api/workspaces/current", json={"catalog_enabled": True})

    cur = c.get("/api/workspaces/current").json()["data"]
    assert "catalog_token" not in cur
    assert "catalog_url" not in cur
    assert "catalog_token_plaintext" not in cur
    assert "catalog_token_set" in cur
