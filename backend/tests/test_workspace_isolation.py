from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient, email: str):
    r = c.post("/api/auth/signup", json={"email": email, "name": "u", "password": "TestPass-2026-Stronk"})
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


# ---------------------------------------------------------------------------
# Helpers for the cross-tenant FK validation tests below.
# ---------------------------------------------------------------------------


def _two_workspaces():
    a = TestClient(app)
    b = TestClient(app)
    _signup(a, f"a-{uuid.uuid4().hex[:6]}@x.com")
    _signup(b, f"b-{uuid.uuid4().hex[:6]}@x.com")
    return a, b


def _create_part(c: TestClient, name: str = "P") -> str:
    r = c.post("/api/parts", json={"name": name, "part_type": "local"})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _create_storage(c: TestClient, name: str = "Bin") -> str:
    r = c.post("/api/storage", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _add_stock(
    c: TestClient,
    *,
    part_id: str,
    storage_id: str | None = None,
    quantity: int = 5,
    lot_name: str = "L",
) -> dict:
    body: dict = {"part_id": part_id, "quantity": quantity, "lot": {"name": lot_name}}
    if storage_id:
        body["storage_location_id"] = storage_id
    r = c.post("/api/stock/add", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]


# ---------------------------------------------------------------------------
# custom_fields: cross-workspace polymorphic write + source-forge attempt
# ---------------------------------------------------------------------------


def test_custom_fields_reject_cross_workspace_object_id():
    """POST /api/custom-fields validates (object_type, object_id) against
    the caller's workspace. Without this, B can attach attributes to A's
    part — own-ws row + foreign object_id pollutes the cross-tenant graph."""
    a, b = _two_workspaces()
    part_a = _create_part(a, "A-secret")

    r = b.post(
        "/api/custom-fields",
        json={"object_type": "part", "object_id": part_a, "key": "k", "value": "v"},
    )
    assert r.status_code == 404, r.text


def test_custom_fields_unknown_object_type_returns_400():
    """The polymorphic registry is the cross-tenant guardrail. Unknown
    object_types must reject with 400 — not silently store an unscoped row."""
    a, _b = _two_workspaces()
    part_a = _create_part(a)
    r = a.post(
        "/api/custom-fields",
        json={"object_type": "lot", "object_id": part_a, "key": "k", "value": "v"},
    )
    assert r.status_code == 400, r.text


def test_custom_fields_source_cannot_be_forged():
    """A caller posting `source: "provider"` would forge a provider-origin
    row, defeating the override/restore UX. The field must be rejected by
    Pydantic (extra='forbid'), so the response is 422 Unprocessable Entity."""
    a, _b = _two_workspaces()
    part = _create_part(a)
    r = a.post(
        "/api/custom-fields",
        json={
            "object_type": "part",
            "object_id": part,
            "key": "k",
            "value": "v",
            "source": "provider",
        },
    )
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# tags: cross-workspace polymorphic write
# ---------------------------------------------------------------------------


def test_tags_reject_cross_workspace_object_id_on_link():
    """POST /api/tags/links validates (object_type, object_id) against the
    caller's workspace. Without this, B can label A's part_id."""
    a, b = _two_workspaces()
    part_a = _create_part(a, "A-secret")
    tag_b = b.post("/api/tags", json={"name": "Hot"}).json()["data"]["id"]
    r = b.post(
        "/api/tags/links",
        json={"tag_id": tag_b, "object_type": "part", "object_id": part_a},
    )
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# stock service: cross-workspace lot/storage on adjust/remove/move.
# adjust_stock was the active leak — `delta = actual_qty - 0` persists a
# positive entry referencing the foreign FK. remove + move are defense in
# depth (today: current_quantity returns 0, raises insufficient).
# ---------------------------------------------------------------------------


def test_stock_adjust_rejects_foreign_lot_and_storage():
    a, b = _two_workspaces()
    storage_a = _create_storage(a, "A-bin")
    part_a = _create_part(a, "A-part")
    lot_a = _add_stock(a, part_id=part_a, storage_id=storage_a, quantity=5)["lot_id"]

    part_b = _create_part(b, "B-part")

    # B passes A's lot_id — must 422 "lot not found", not silently persist.
    r = b.post(
        "/api/stock/adjust",
        json={
            "part_id": part_b,
            "lot_id": lot_a,
            "actual_quantity": 99,
        },
    )
    assert r.status_code == 400, r.text

    # B passes A's storage_id — same.
    r = b.post(
        "/api/stock/adjust",
        json={
            "part_id": part_b,
            "storage_location_id": storage_a,
            "actual_quantity": 99,
        },
    )
    assert r.status_code == 400, r.text


def test_stock_remove_rejects_foreign_lot_and_storage():
    a, b = _two_workspaces()
    storage_a = _create_storage(a, "A-bin")
    part_a = _create_part(a, "A-part")
    lot_a = _add_stock(a, part_id=part_a, storage_id=storage_a, quantity=5)["lot_id"]

    part_b = _create_part(b, "B-part")
    _add_stock(b, part_id=part_b, quantity=10)

    r = b.post(
        "/api/stock/remove",
        json={"part_id": part_b, "quantity": 1, "lot_id": lot_a},
    )
    assert r.status_code == 400, r.text

    r = b.post(
        "/api/stock/remove",
        json={"part_id": part_b, "quantity": 1, "storage_location_id": storage_a},
    )
    assert r.status_code == 400, r.text


def test_stock_move_rejects_foreign_source():
    a, b = _two_workspaces()
    storage_a = _create_storage(a, "A-bin")
    part_a = _create_part(a, "A-part")
    lot_a = _add_stock(a, part_id=part_a, storage_id=storage_a, quantity=5)["lot_id"]

    part_b = _create_part(b, "B-part")
    storage_b1 = _create_storage(b, "B-bin1")
    storage_b2 = _create_storage(b, "B-bin2")
    _add_stock(b, part_id=part_b, storage_id=storage_b1, quantity=5)

    # B tries to move using A's source_storage_location_id
    r = b.post(
        "/api/stock/move",
        json={
            "part_id": part_b,
            "quantity": 1,
            "source_storage_location_id": storage_a,
            "destination_storage_location_id": storage_b2,
        },
    )
    assert r.status_code == 400, r.text

    # B tries to move using A's source_lot_id
    r = b.post(
        "/api/stock/move",
        json={
            "part_id": part_b,
            "quantity": 1,
            "source_lot_id": lot_a,
            "destination_storage_location_id": storage_b2,
        },
    )
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# parts.default_storage_location_id on create / patch / bulk-import-from-scan
# ---------------------------------------------------------------------------


def test_parts_create_rejects_foreign_default_storage():
    a, b = _two_workspaces()
    storage_a = _create_storage(a)

    r = b.post(
        "/api/parts",
        json={
            "name": "X",
            "part_type": "local",
            "default_storage_location_id": storage_a,
        },
    )
    assert r.status_code == 404, r.text


def test_parts_patch_rejects_foreign_default_storage():
    a, b = _two_workspaces()
    storage_a = _create_storage(a)
    part_b = _create_part(b)

    r = b.patch(
        f"/api/parts/{part_b}",
        json={"default_storage_location_id": storage_a},
    )
    assert r.status_code == 404, r.text


def test_parts_bulk_import_rejects_foreign_storage():
    """bulk-import-from-scan must validate row.storage_location_id against
    the caller's workspace BEFORE creating the part. The whole-batch loop
    keeps running; only the offending row is reported as `invalid`."""
    a, b = _two_workspaces()
    storage_a = _create_storage(a)

    # B has to have a provider configured for bulk-import to even reach the
    # row-validation step (the endpoint short-circuits if no provider). Use
    # the "none" path — set parts_provider to mouser and a fake key just
    # so the configuration check passes; the foreign-storage validation
    # fires before the provider lookup.
    r = b.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": "x" * 36},
    )
    assert r.status_code == 200, r.text

    r = b.post(
        "/api/parts/bulk-import-from-scan",
        json={
            "rows": [
                # Row 1: foreign storage — must be marked invalid, not block the loop.
                {
                    "mpn": "RC0402-1K",
                    "storage_location_id": storage_a,
                    "quantity": 1,
                },
                # Row 2: no storage at all — proves the per-row independence;
                # mpn won't resolve (fake key, no real provider) so it ends as
                # lookup_failed, but it MUST run rather than be skipped because
                # row 1 failed.
                {"mpn": "RC0402-2K", "quantity": 1},
            ]
        },
    )
    # Endpoint returns 200 with per-row outcomes.
    assert r.status_code == 200, r.text
    out = r.json()["data"]["rows"]
    assert len(out) == 2
    assert out[0]["status"] == "invalid"
    assert "storage" in (out[0].get("error") or "").lower()
    # Row 2 was processed (per-row independence) and reached the provider,
    # which fails because the API key is fake — but the loop did NOT abort
    # on row 1.
    assert out[1]["status"] == "lookup_failed"


# ---------------------------------------------------------------------------
# builds.consume: cross-workspace lot / storage on a consume line.
# Defense in depth — current_quantity ws-filter would return 0 today,
# blocking the write. Pinned so a future refactor can't reopen the leak.
# ---------------------------------------------------------------------------


def test_builds_consume_rejects_foreign_lot_and_storage():
    a, b = _two_workspaces()
    storage_b = _create_storage(b, "B-bin")
    part_b = _create_part(b, "B-part")
    lot_b = _add_stock(b, part_id=part_b, storage_id=storage_b, quantity=5)["lot_id"]

    # A sets up a project + entry pointing at A's part, then a build.
    storage_a = _create_storage(a, "A-bin")
    part_a = _create_part(a, "A-part")
    _add_stock(a, part_id=part_a, storage_id=storage_a, quantity=10)

    proj_a = a.post("/api/projects", json={"name": "P"}).json()["data"]["id"]
    entry = a.post(
        f"/api/projects/{proj_a}/entries",
        json={"entry_type": "part", "part_id": part_a, "quantity": 1},
    ).json()["data"]["id"]
    build_a = a.post(
        "/api/builds",
        json={"name": "B1", "project_id": proj_a, "quantity": 1},
    ).json()["data"]["id"]

    # A's build, A's entry, A's part — but the line claims B's lot. Must reject.
    r = a.post(
        f"/api/builds/{build_a}/consume",
        json={
            "lines": [
                {
                    "project_entry_id": entry,
                    "part_id": part_a,
                    "lot_id": lot_b,
                    "quantity": 1,
                }
            ]
        },
    )
    assert r.status_code == 400, r.text

    # And likewise for storage_location_id.
    r = a.post(
        f"/api/builds/{build_a}/consume",
        json={
            "lines": [
                {
                    "project_entry_id": entry,
                    "part_id": part_a,
                    "storage_location_id": storage_b,
                    "quantity": 1,
                }
            ]
        },
    )
    assert r.status_code == 400, r.text
