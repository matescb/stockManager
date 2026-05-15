from __future__ import annotations

import json

from app.api.routes import sentry_tunnel as sentry_tunnel_route


def _remove_default_origin(client):
    client.headers.pop("Origin", None)


def test_unauthenticated_cross_origin_rejected(client, monkeypatch):
    monkeypatch.setattr(
        sentry_tunnel_route,
        "ALLOWED_ENDPOINTS",
        (("o123.ingest.sentry.io", "456"),),
    )

    envelope_header = json.dumps({"dsn": "https://abc@o123.ingest.sentry.io/456"})
    response = client.post(
        "/api/sentry-tunnel",
        content=envelope_header.encode() + b"\n{}",
        headers={"Origin": "https://evil.example.com"},
    )

    assert response.status_code in {401, 403}, response.text
    body = response.json()
    assert body["data"] is None
    assert body["status"]["category"] in {"unauthenticated", "forbidden"}


def test_referer_fallback_when_origin_missing(client, monkeypatch):
    monkeypatch.setattr(sentry_tunnel_route, "ALLOWED_ENDPOINTS", ())
    _remove_default_origin(client)

    response = client.post(
        "/api/sentry-tunnel",
        content=b'{"dsn":"https://abc@o123.ingest.sentry.io/456"}\n{}',
        headers={"Referer": "http://testserver/login"},
    )

    assert response.status_code == 204, response.text


def test_cross_origin_referer_rejected_when_origin_missing(client, monkeypatch):
    monkeypatch.setattr(sentry_tunnel_route, "ALLOWED_ENDPOINTS", ())
    _remove_default_origin(client)

    response = client.post(
        "/api/sentry-tunnel",
        content=b'{"dsn":"https://abc@o123.ingest.sentry.io/456"}\n{}',
        headers={"Referer": "https://evil.example.com/login"},
    )

    assert response.status_code == 401, response.text
    body = response.json()
    assert body["data"] is None
    assert body["status"]["category"] == "unauthenticated"


def test_referer_not_used_when_origin_present(client, monkeypatch):
    monkeypatch.setattr(sentry_tunnel_route, "ALLOWED_ENDPOINTS", ())

    response = client.post(
        "/api/sentry-tunnel",
        content=b'{"dsn":"https://abc@o123.ingest.sentry.io/456"}\n{}',
        headers={
            "Origin": "https://evil.example.com",
            "Referer": "http://testserver/login",
        },
    )

    assert response.status_code == 401, response.text
