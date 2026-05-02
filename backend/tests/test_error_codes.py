"""Error-code contract tests for the PR2a migration (issue #251).

Each test hits a route that was migrated from `raise HTTPException(…)` to
`raise_http(…)` and asserts that:
  - the HTTP status is unchanged, and
  - `body["code"]` equals the expected ErrorCodes constant.

Covered routers (domain-light group):
  lots, storage, attachments, _parts_shared (via parts), projects,
  bom_presets, reports, catalog, custom_fields.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.errors import ErrorCodes
from app.main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assert_code(body: dict, expected_code: str) -> None:
    assert body.get("code") == expected_code, (
        f"expected code={expected_code!r}, got body={body!r}"
    )


def _signup(c: TestClient, email: str | None = None) -> dict:
    email = email or f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "Tester", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


@pytest.fixture
def c(db):
    """Authenticated client tied to the per-test rolled-back DB."""
    client = TestClient(app)
    _signup(client)
    return client


# ---------------------------------------------------------------------------
# lots.py
# ---------------------------------------------------------------------------

def test_lot_not_found(c):
    r = c.get(f"/api/lots/{uuid.uuid4()}")
    assert r.status_code == 404
    _assert_code(r.json(), ErrorCodes.LOT_NOT_FOUND)


def test_lot_patch_invalid_expiration_date(c):
    # Create a part + add stock which creates a lot.
    r_part = c.post("/api/parts", json={"name": "P", "part_type": "local"})
    assert r_part.status_code in (200, 201)
    part_id = r_part.json()["data"]["id"]
    r_stock = c.post("/api/stock/add", json={"part_id": part_id, "quantity": 5, "lot": {"name": "L1"}})
    assert r_stock.status_code == 200, r_stock.text
    lot_id = r_stock.json()["data"]["lot_id"]

    r = c.patch(f"/api/lots/{lot_id}", json={"expiration_date": "not-a-date"})
    assert r.status_code == 400
    _assert_code(r.json(), ErrorCodes.LOT_INVALID_EXPIRATION_DATE)


# ---------------------------------------------------------------------------
# storage.py
# ---------------------------------------------------------------------------

def test_storage_not_found(c):
    r = c.get(f"/api/storage/{uuid.uuid4()}")
    assert r.status_code == 404
    _assert_code(r.json(), ErrorCodes.STORAGE_NOT_FOUND)


def test_storage_archive_has_stock(c):
    # Create part + storage + put stock in storage.
    part_id = c.post("/api/parts", json={"name": "P", "part_type": "local"}).json()["data"]["id"]
    storage_id = c.post("/api/storage", json={"name": "Bin"}).json()["data"]["id"]
    c.post("/api/stock/add", json={"part_id": part_id, "quantity": 3, "storage_location_id": storage_id})

    r = c.post(f"/api/storage/{storage_id}/archive")
    assert r.status_code == 409
    body = r.json()
    _assert_code(body, ErrorCodes.STORAGE_HAS_STOCK)
    # Extra field should be spread onto the body.
    assert "blocking" in body


# ---------------------------------------------------------------------------
# _parts_shared.py (surfaced through the parts router)
# ---------------------------------------------------------------------------

def test_part_not_found(c):
    r = c.get(f"/api/parts/{uuid.uuid4()}")
    assert r.status_code == 404
    _assert_code(r.json(), ErrorCodes.PART_NOT_FOUND)


# ---------------------------------------------------------------------------
# projects.py
# ---------------------------------------------------------------------------

def test_project_not_found(c):
    r = c.get(f"/api/projects/{uuid.uuid4()}")
    assert r.status_code == 404
    _assert_code(r.json(), ErrorCodes.PROJECT_NOT_FOUND)


def test_project_add_entry_archived_part(c):
    # Create a part, archive it, then try to add it to a BOM.
    part_id = c.post("/api/parts", json={"name": "P", "part_type": "local"}).json()["data"]["id"]
    c.post(f"/api/parts/{part_id}/archive")
    project_id = c.post("/api/projects", json={"name": "Proj"}).json()["data"]["id"]

    r = c.post(
        f"/api/projects/{project_id}/entries",
        json={"part_id": part_id, "quantity": 1, "entry_type": "part"},
    )
    assert r.status_code == 404
    _assert_code(r.json(), ErrorCodes.PART_NOT_FOUND)


# ---------------------------------------------------------------------------
# bom_presets.py
# ---------------------------------------------------------------------------

def test_bom_preset_not_found(c):
    r = c.get(f"/api/bom-presets/{uuid.uuid4()}")
    assert r.status_code == 404
    _assert_code(r.json(), ErrorCodes.BOM_PRESET_NOT_FOUND)


# ---------------------------------------------------------------------------
# reports.py
# ---------------------------------------------------------------------------

def test_report_project_not_found(c):
    r = c.get(f"/api/reports/bom-shortage?project_id={uuid.uuid4()}")
    assert r.status_code == 404
    _assert_code(r.json(), ErrorCodes.REPORT_PROJECT_NOT_FOUND)


# ---------------------------------------------------------------------------
# catalog.py
# ---------------------------------------------------------------------------

def test_catalog_not_found_bad_token(db):
    """A bogus token returns 404 with catalog.not_found code."""
    c = TestClient(app)
    r = c.get("/catalog/definitely-not-a-real-token")
    assert r.status_code == 404
    # The catalog endpoint returns HTML on GET /{token} but JSON on /{token}/parts.json.
    r2 = c.get("/catalog/definitely-not-a-real-token/parts.json")
    assert r2.status_code == 404
    _assert_code(r2.json(), ErrorCodes.CATALOG_NOT_FOUND)


# ---------------------------------------------------------------------------
# custom_fields.py
# ---------------------------------------------------------------------------

def test_custom_field_not_override(c):
    """Calling DELETE /{cf_id}/override on a manual row returns 400
    with custom_field.not_override code."""
    part_id = c.post("/api/parts", json={"name": "P", "part_type": "local"}).json()["data"]["id"]
    # Create a manual custom field.
    r_cf = c.post(
        "/api/custom-fields",
        json={"object_type": "part", "object_id": part_id, "key": "mykey", "value": "v"},
    )
    assert r_cf.status_code == 201, r_cf.text
    cf_id = r_cf.json()["data"]["id"]

    r = c.delete(f"/api/custom-fields/{cf_id}/override")
    assert r.status_code == 400
    _assert_code(r.json(), ErrorCodes.CUSTOM_FIELD_NOT_OVERRIDE)
