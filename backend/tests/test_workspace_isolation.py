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


def test_attachments_reject_cross_workspace_object_id():
    """Polymorphic write check on /attachments. Without it, a caller in
    workspace B can attach a file keyed to a part_id owned by workspace A —
    the FK enforces existence, not access."""
    a = TestClient(app)
    b = TestClient(app)
    _signup(a, f"a-{uuid.uuid4().hex[:6]}@x.com")
    _signup(b, f"b-{uuid.uuid4().hex[:6]}@x.com")

    # A creates a part
    r = a.post("/api/parts", json={"name": "A's part", "part_type": "local"})
    assert r.status_code in (200, 201)
    part_a = r.json()["data"]["id"]

    # B tries to attach a file to A's part_id — must 404
    files = {"file": ("s.txt", b"secret", "text/plain")}
    data = {"object_type": "part", "object_id": part_a, "file_type": "other"}
    r = b.post("/api/attachments", data=data, files=files)
    assert r.status_code == 404, r.text

    # And A's by-object listing for that part is still empty (no leak the other way).
    r = a.get(f"/api/attachments/by-object/part/{part_a}")
    assert r.status_code == 200
    assert r.json()["data"] == []


def test_project_entry_rejects_cross_workspace_part_id():
    """Adding/patching a BOM entry must not accept a part_id from another
    workspace. Without this, A could embed B's part UUID in their BOM and
    leak it through downstream joins (build consume, BOM-shortage report)."""
    a = TestClient(app)
    b = TestClient(app)
    _signup(a, f"a-{uuid.uuid4().hex[:6]}@x.com")
    _signup(b, f"b-{uuid.uuid4().hex[:6]}@x.com")

    # B owns a secret part
    r = b.post("/api/parts", json={"name": "B-Secret", "part_type": "local"})
    assert r.status_code in (200, 201)
    secret = r.json()["data"]["id"]

    # A creates a project
    r = a.post("/api/projects", json={"name": "P"})
    assert r.status_code in (200, 201)
    proj = r.json()["data"]["id"]

    # A tries to attach B's part directly — must 404
    r = a.post(
        f"/api/projects/{proj}/entries",
        json={"entry_type": "part", "part_id": secret, "quantity": 1},
    )
    assert r.status_code == 404, r.text

    # A also can't smuggle it in via meta_part_id
    r = a.post(
        f"/api/projects/{proj}/entries",
        json={"entry_type": "meta_part", "meta_part_id": secret, "quantity": 1},
    )
    assert r.status_code == 404, r.text

    # A creates a legitimate unmatched entry, then tries to patch in B's part — must 404
    r = a.post(
        f"/api/projects/{proj}/entries",
        json={"entry_type": "unmatched", "name": "x", "quantity": 1},
    )
    assert r.status_code in (200, 201)
    entry = r.json()["data"]["id"]

    r = a.patch(f"/api/projects/{proj}/entries/{entry}", json={"part_id": secret})
    assert r.status_code == 404, r.text

    r = a.patch(f"/api/projects/{proj}/entries/{entry}", json={"meta_part_id": secret})
    assert r.status_code == 404, r.text

    # And the existing /match endpoint still rejects the same vector (regression
    # pin — it was already correct, this asserts it stays that way).
    r = a.post(f"/api/projects/{proj}/entries/{entry}/match", json={"part_id": secret})
    assert r.status_code == 404, r.text
