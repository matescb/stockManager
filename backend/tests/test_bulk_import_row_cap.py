"""Tests for the row-count cap on bulk-import-from-scan (BE2-003).

The schema caps `rows` at max_length=50. POSTing 51+ rows must return
422 with a field-level error pointing at `rows`.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app


def _signup_with_mouser(c: TestClient) -> str:
    r = c.post(
        "/api/auth/signup",
        json={"email": f"u-{uuid.uuid4().hex[:8]}@x.com", "name": "u", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text
    c.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": "fake-key"},
    )
    return r.json()["data"]["workspace_id"]


def test_bulk_import_rejects_51_rows():
    """51 rows exceeds the cap of 50 → 422 with error on `rows` field."""
    c = TestClient(app)
    _signup_with_mouser(c)

    rows = [{"mpn": f"MPN-{i:04d}"} for i in range(51)]
    r = c.post("/api/parts/bulk-import-from-scan", json={"rows": rows})
    assert r.status_code == 422, r.text
    # This app wraps 422 in {data, status, errors}. The `errors` list has
    # `field` (dot-joined path) and `message`. Check that "rows" appears
    # somewhere in the field path to confirm it's the cap that fired.
    body = r.json()
    errors = body.get("errors") or []
    fields = [e.get("field", "") for e in errors]
    assert any("rows" in f for f in fields), f"Expected 'rows' in error fields; got {fields!r} | body: {body}"


def test_bulk_import_accepts_50_rows(monkeypatch):
    """Exactly 50 rows is the boundary — must be accepted (not rejected)."""
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: {
            "Errors": [],
            "SearchResults": {
                "Parts": [
                    {
                        "Manufacturer": "Yageo",
                        "ManufacturerPartNumber": payload["SearchByPartRequest"]["mouserPartNumber"],
                        "Description": "Resistor",
                        "DataSheetUrl": None,
                        "ImagePath": None,
                        "ProductDetailUrl": "https://example.com",
                        "ProductAttributes": [],
                    }
                ]
            },
        },
    )
    c = TestClient(app)
    _signup_with_mouser(c)

    rows = [{"mpn": f"MPN-{i:04d}"} for i in range(50)]
    r = c.post("/api/parts/bulk-import-from-scan", json={"rows": rows})
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["summary"]["created"] == 50
