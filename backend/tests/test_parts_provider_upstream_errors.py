from __future__ import annotations

import uuid
from contextlib import contextmanager

import httpx
from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient) -> None:
    r = c.post(
        "/api/auth/signup",
        json={
            "email": f"u-{uuid.uuid4().hex[:8]}@x.com",
            "name": "u",
            "password": "TestPass-2026-Stronk",
        },
    )
    assert r.status_code == 200, r.text


def _reset_provider_state(provider_name: str) -> None:
    import app.domain.parts.services.provider_cache as _cache

    _cache._cache._store.clear()
    _cache._breakers.pop(provider_name, None)


def _authed_client() -> TestClient:
    c = TestClient(app)
    _signup(c)
    return c


def _enable_mouser(client: TestClient) -> None:
    r = client.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": "fake-key"},
    )
    assert r.status_code == 200, r.text


def _enable_digikey(client: TestClient) -> None:
    r = client.patch(
        "/api/workspaces/current",
        json={
            "parts_provider": "digikey",
            "parts_provider_api_key": "fake-client-id",
            "parts_provider_api_secret": "fake-client-secret",
        },
    )
    assert r.status_code == 200, r.text


def test_mouser_503_returns_502(monkeypatch):
    _reset_provider_state("mouser")
    c = _authed_client()
    _enable_mouser(c)

    @contextmanager
    def fake_client(*, provider_name: str, timeout: float):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"message": "maintenance"})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            yield client

    monkeypatch.setattr(
        "app.domain.parts.providers.mouser.make_retrying_client",
        fake_client,
    )

    r = c.post("/api/parts/lookup-mpn", json={"mpn": "RC0402JR-070R"})
    assert r.status_code == 502, r.text
    body = r.json()
    assert body["data"] is None
    assert body["status"]["category"] == "server_error"
    assert "HTTP 503" in body["status"]["message"]
    assert body["provider"] == "mouser"


def test_digikey_timeout_returns_502(monkeypatch):
    _reset_provider_state("digikey")
    c = _authed_client()
    _enable_digikey(c)

    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._post_token",
        lambda client_id, client_secret: {"access_token": "tok", "expires_in": 600},
    )

    def timeout(token: str, client_id: str, mpn: str) -> tuple[int, dict]:
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._get_product_details",
        timeout,
    )

    r = c.post("/api/parts/lookup-mpn", json={"mpn": "TXU0204QWBQARQ1"})
    assert r.status_code == 502, r.text
    body = r.json()
    assert body["data"] is None
    assert body["status"]["category"] == "server_error"
    assert "ReadTimeout" in body["status"]["message"]
    assert body["provider"] == "digikey"


def test_mouser_no_match_still_returns_200(monkeypatch):
    _reset_provider_state("mouser")
    c = _authed_client()
    _enable_mouser(c)

    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: {
            "Errors": [],
            "SearchResults": {"NumberOfResult": 0, "Parts": []},
        },
    )

    r = c.post("/api/parts/lookup-mpn", json={"mpn": "DOES-NOT-EXIST"})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["found"] is False
    assert "no match" in body["message"].lower()
    assert body["provider"] == "mouser"
