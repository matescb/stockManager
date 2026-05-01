from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

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


def _enable_catalog(client: TestClient) -> str:
    """Flip catalog on and return the public URL (path)."""
    r = client.patch("/api/workspaces/current", json={"catalog_enabled": True})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["catalog_enabled"] is True
    assert data["catalog_url"], "expected catalog_url to be set when enabled"
    return data["catalog_url"]


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
    assert cur["catalog_url"] is None


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
    assert r.json()["data"]["catalog_url"] is None

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
    new_url = r.json()["data"]["catalog_url"]
    assert new_url and new_url != old_url

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
