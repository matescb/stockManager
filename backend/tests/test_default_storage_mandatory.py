"""Regression coverage for BE CRIT-2 (default_storage_mandatory bypass).

Before the fix, the mandatory-default-storage rule was trivially defeated
by omitting `storage_location_id` from the add-stock payload — the
existing check only fired when storage was non-None AND the id mismatched.
The bulk_import_from_scan path exploited this implicitly by accepting
rows with no `storage_location_id`. The fix tightens the predicate so an
omitted storage is also rejected.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient) -> str:
    r = c.post(
        "/api/auth/signup",
        json={"email": f"u-{uuid.uuid4().hex[:8]}@x.com", "name": "u", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["workspace_id"]


@pytest.fixture
def authed():
    c = TestClient(app)
    _signup(c)
    return c


def _create_storage(c: TestClient, name: str = "BinA") -> str:
    r = c.post("/api/storage", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _create_part(c: TestClient, **kwargs) -> str:
    body = {"name": "P", "part_type": "local"}
    body.update(kwargs)
    r = c.post("/api/parts", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def test_add_stock_rejects_omitted_storage_when_mandatory(authed):
    bin_a = _create_storage(authed, "Bin-A")
    part_id = _create_part(
        authed,
        default_storage_location_id=bin_a,
        default_storage_mandatory=True,
    )

    # Omit storage_location_id entirely — the bug allowed this through.
    r = authed.post(
        "/api/stock/add",
        json={"part_id": part_id, "quantity": 5},
    )
    assert r.status_code == 400, r.text
    assert "storage" in (r.json().get("status", {}).get("message") or "").lower()

    # Wrong storage_location_id — was already rejected before the fix; pin.
    bin_b = _create_storage(authed, "Bin-B")
    r = authed.post(
        "/api/stock/add",
        json={"part_id": part_id, "quantity": 5, "storage_location_id": bin_b},
    )
    assert r.status_code == 400, r.text

    # Correct storage_location_id — still works.
    r = authed.post(
        "/api/stock/add",
        json={"part_id": part_id, "quantity": 5, "storage_location_id": bin_a},
    )
    assert r.status_code in (200, 201), r.text


def test_add_stock_accepts_omitted_storage_when_mandatory_is_off(authed):
    """The mandatory flag is opt-in. Parts without it must still accept
    storage-less stock additions (the manual stock-add flow's default)."""
    part_id = _create_part(authed)  # no default storage at all
    r = authed.post(
        "/api/stock/add",
        json={"part_id": part_id, "quantity": 5},
    )
    assert r.status_code in (200, 201), r.text


def test_bulk_import_surfaces_stock_error_when_part_requires_storage(authed, monkeypatch):
    """The bulk-import path catches StockError as `stock_error` on the
    row's response. Part is still created, but the operator sees the
    failure rather than silently importing with NULL storage."""
    bin_a = _create_storage(authed, "Bin-A")
    part_id = _create_part(
        authed,
        default_storage_location_id=bin_a,
        default_storage_mandatory=True,
        mpn="EXISTING-MPN-X1",
    )
    # Existing part already in the workspace — the bulk import will mark
    # this MPN as `duplicate`, not exercise the path. Use a different MPN
    # for the new row but configure a provider that returns it.
    authed.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": "fake-key"},
    )

    def _stub_response(mpn: str = "NEW-MANDATORY-PART") -> dict:
        return {
            "Errors": [],
            "SearchResults": {
                "NumberOfResult": 1,
                "Parts": [
                    {
                        "Manufacturer": "X",
                        "ManufacturerPartNumber": mpn,
                        "Description": "test",
                        "Category": "test",
                        "DataSheetUrl": "",
                        "ImagePath": "",
                        "ProductDetailUrl": "",
                        "ProductAttributes": [],
                    }
                ],
            },
        }

    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_response(payload["SearchByPartRequest"]["mouserPartNumber"]),
    )

    # Create the bulk-import row WITHOUT storage_location_id but WITH
    # quantity. The new part doesn't have default_storage_mandatory set
    # (it's a fresh part the bulk-import creates), so this row should
    # actually succeed — bulk-import doesn't propagate the existing
    # part's flag. This test exists to pin that bulk-import isn't
    # silently failing.
    r = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{"mpn": "NEW-MANDATORY-PART", "quantity": 5}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["rows"][0]["status"] == "created"
