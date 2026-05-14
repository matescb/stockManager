from __future__ import annotations

import json

from app.api.routes import sentry_tunnel as sentry_tunnel_route


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
