from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient, email: str):
    r = c.post("/api/auth/signup", json={"email": email, "name": "u", "password": "password123"})
    assert r.status_code == 200, r.text
    return r.json()["data"]["workspace_id"]


def test_workspace_isolation():
    a = TestClient(app)
    b = TestClient(app)
    ws_a = _signup(a, f"a-{uuid.uuid4().hex[:6]}@x.com")
    ws_b = _signup(b, f"b-{uuid.uuid4().hex[:6]}@x.com")
    assert ws_a != ws_b

    # User A creates a part
    r = a.post("/api/parts", json={"name": "Secret Cap", "part_type": "local"})
    assert r.status_code in (200, 201)
    part_id = r.json()["data"]["id"]

    # User B should not see it
    r = b.get("/api/parts")
    assert r.status_code == 200
    assert all(p["id"] != part_id for p in r.json()["data"])

    # User B trying to GET it directly → 404
    r = b.get(f"/api/parts/{part_id}")
    assert r.status_code == 404
