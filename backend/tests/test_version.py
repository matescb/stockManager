"""Contract tests for `GET /api/version`.

The route exists so the frontend `/about` page can show the backend's
build id next to the one Vite inlined into the SPA bundle. With no
staging environment and an auto-deploy that rebuilds the two images
separately, the two SHAs disagreeing is the cheapest signal that a
deploy half-applied — so the shape of this response is load-bearing for
diagnosis, not decoration.

Three things are pinned:
  * the `{data, status}` envelope (CLAUDE.md hard invariant),
  * the value being `SENTRY_RELEASE`, with `""` normalised to `None`,
  * the route requiring a credential — unlike `/api/health`, which the
    compose healthcheck and the post-deploy CI gate must reach anonymously.
"""
from __future__ import annotations

import pytest

from app.core.config import settings


def test_version_requires_authentication(client):
    """Unlike `/api/health`, a build fingerprint is not public. Nothing in
    the deploy path reads this route without a credential, so the cheap
    posture is the closed one."""
    r = client.get("/api/version")
    assert r.status_code == 401, r.text


def test_version_returns_the_envelope(authed_client):
    r = authed_client.get("/api/version")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"data", "status"}
    assert body["status"]["category"] == "ok"
    assert set(body["data"].keys()) == {"build"}


def test_version_reports_sentry_release(authed_client, monkeypatch):
    """The value is whatever the deploy exported as SENTRY_RELEASE — the
    same 12-char short SHA `VITE_APP_VERSION` carries into the bundle."""
    current = settings()
    monkeypatch.setattr(current, "SENTRY_RELEASE", "0123456789ab", raising=False)
    r = authed_client.get("/api/version")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["build"] == "0123456789ab"


def test_version_reports_null_when_unset(authed_client, monkeypatch):
    """Outside a CI deploy `SENTRY_RELEASE` is `""`. Report `null` so the
    client can distinguish "not built by CI" from a real identifier rather
    than rendering an empty string as if it were a version."""
    current = settings()
    monkeypatch.setattr(current, "SENTRY_RELEASE", "", raising=False)
    r = authed_client.get("/api/version")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["build"] is None


@pytest.mark.parametrize("path", ["/api/health", "/api/version"])
def test_both_build_probes_are_registered(client, path):
    """Guards against the route silently disappearing in a refactor: the
    About page's mismatch banner is only meaningful while both halves
    answer."""
    r = client.get(path)
    assert r.status_code != 404, f"{path} is not routed"
