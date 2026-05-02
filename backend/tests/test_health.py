"""Tests for the upgraded `/api/health` endpoint (INFRA2-002 / Infra HIGH-1).

The route now executes a `SELECT 1` against the engine and checks
write-access to `UPLOAD_DIR`. We pin both the happy path and the
controlled failure modes — the post-deploy CI gate, the docker compose
healthcheck, and any future external probe all rely on these contracts.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_returns_200_when_db_and_uploads_ok(client):
    """Conftest stands up the test Postgres + an UPLOAD_DIR; a healthy
    suite-startup is the contract this test pins for the CI gate."""
    r = client.get("/api/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["data"]["status"] == "ok"
    assert body["data"]["db"] == "ok"
    assert body["data"]["uploads"] == "ok"
    assert body["status"]["category"] == "ok"


def test_health_returns_503_when_uploads_dir_unwritable(client):
    """If the uploads volume isn't writable (lost mount, perms drift),
    the route reports 503 with structured detail so the operator sees
    `uploads: not writable: <path>` in the failure log instead of a
    bare 500."""
    with patch("app.main.os.access", return_value=False):
        r = client.get("/api/health")
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["data"] is None
    assert body["status"]["category"] == "server_error"
    assert "uploads" in body
    assert "not writable" in body["uploads"]
    assert body["db"] == "ok"


def test_health_returns_503_when_db_query_raises(client):
    """A DB outage during health probe shows up as 503 with the
    exception type name — operator gets a concrete signal rather than
    the route crashing silently or returning 200 on a dead DB."""
    from app.infra.db import get_engine

    real_engine = get_engine()

    class _BrokenEngine:
        def connect(self):
            raise RuntimeError("simulated DB outage")

    with patch("app.main.os.access", wraps=lambda *a, **kw: True):
        with patch("app.infra.db.get_engine", return_value=_BrokenEngine()):
            r = client.get("/api/health")

    assert real_engine is not None  # sanity — we didn't actually break the suite
    assert r.status_code == 503, r.text
    body = r.json()
    assert "error: RuntimeError" in body["db"]
    assert body["uploads"] == "ok"


def test_health_envelope_shape_on_failure(client):
    """The `{data, status}` envelope contract from CLAUDE.md must hold
    on the 503 path too. The operator's tooling (and the post-deploy
    curl gate) parses the body the same way as any other endpoint."""
    with patch("app.main.os.access", return_value=False):
        r = client.get("/api/health")
    body = r.json()
    assert set(body.keys()) >= {"data", "status"}
    assert body["data"] is None
    assert "category" in body["status"]
    assert "message" in body["status"]
