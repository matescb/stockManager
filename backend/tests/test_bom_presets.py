from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def authed():
    c = TestClient(app)
    r = c.post(
        "/api/auth/signup",
        json={"email": f"u-{uuid.uuid4().hex[:8]}@x.com", "name": "u", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text
    return c


def test_preset_crud(authed):
    c = authed
    # Empty list
    assert c.get("/api/bom-presets").json()["data"] == []

    # Create
    cfg = {
        "separator": ";",
        "encoding": "utf-8",
        "has_header": True,
        "designator_separator": ",",
        "mapping": [
            {"column_index": 0, "target": "quantity"},
            {"column_index": 1, "target": "mpn"},
        ],
    }
    r = c.post("/api/bom-presets", json={"name": "Altium semicolon", "config": cfg})
    assert r.status_code == 201, r.text
    pid = r.json()["data"]["id"]
    assert r.json()["data"]["config"] == cfg

    # List
    rows = c.get("/api/bom-presets").json()["data"]
    assert len(rows) == 1
    assert rows[0]["id"] == pid

    # Patch name
    r = c.patch(f"/api/bom-presets/{pid}", json={"name": "Altium ';'"})
    assert r.json()["data"]["name"] == "Altium ';'"

    # Patch config
    cfg2 = {**cfg, "designator_separator": " "}
    r = c.patch(f"/api/bom-presets/{pid}", json={"config": cfg2})
    assert r.json()["data"]["config"]["designator_separator"] == " "

    # Delete
    r = c.delete(f"/api/bom-presets/{pid}")
    assert r.status_code == 200
    assert c.get("/api/bom-presets").json()["data"] == []


def test_preset_isolated_per_workspace(authed):
    c = authed
    c.post("/api/bom-presets", json={"name": "P1", "config": {"x": 1}})

    # New user → new workspace → can't see it
    other = TestClient(app)
    other.post(
        "/api/auth/signup",
        json={"email": f"u-{uuid.uuid4().hex[:8]}@x.com", "name": "u2", "password": "TestPass-2026-Stronk"},
    )
    rows = other.get("/api/bom-presets").json()["data"]
    assert rows == []
