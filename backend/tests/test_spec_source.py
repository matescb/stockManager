from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient) -> str:
    r = c.post(
        "/api/auth/signup",
        json={"email": f"u-{uuid.uuid4().hex[:8]}@x.com", "name": "u", "password": "password123"},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["workspace_id"]


def _enable_mouser(c: TestClient, key: str = "fake-key"):
    r = c.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": key},
    )
    assert r.status_code == 200, r.text


def _create_part(c: TestClient, name: str = "Cap", mpn: str | None = None) -> str:
    r = c.post(
        "/api/parts",
        json={
            "name": name,
            "part_type": "linked" if mpn else "local",
            "mpn": mpn,
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


@pytest.fixture
def authed():
    c = TestClient(app)
    _signup(c)
    return c


# ---------------------------------------------------------------------------
# refresh-from-provider
# ---------------------------------------------------------------------------


_FAKE_PART = {
    "Manufacturer": "YAGEO",
    "ManufacturerPartNumber": "RC0402JR-070R",
    "Description": "0R 0402",
    "Category": "Resistors",
    "DataSheetUrl": "https://example.com/ds.pdf",
    "ImagePath": "https://example.com/img.jpg",
    "ProductDetailUrl": "https://www.mouser.com/...",
    "ProductAttributes": [
        {"AttributeName": "Resistance", "AttributeValue": "0 Ohms"},
        {"AttributeName": "Tolerance", "AttributeValue": "5 %"},
        {"AttributeName": "Package / Case", "AttributeValue": "0402"},
    ],
}


def _stub_response(part: dict | None = None):
    if part is None:
        part = _FAKE_PART
    return {"Errors": [], "SearchResults": {"NumberOfResult": 1, "Parts": [part]}}


def test_refresh_writes_provider_rows_and_links_part(authed, monkeypatch):
    _enable_mouser(authed)
    part_id = _create_part(authed, "Resistor", "RC0402JR-070R")

    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_response(),
    )
    r = authed.post(f"/api/parts/{part_id}/refresh-from-provider")
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["found"] is True
    assert body["provider"] == "mouser"
    assert body["summary"]["added"] >= 5  # 3 specs + image_url + datasheet_url
    p = body["part"]
    assert p["manufacturer"] == "YAGEO"
    assert p["linked_provider"] == "mouser"
    assert p["last_refresh_at"] is not None
    assert p["description_locally_edited"] is False

    rows = authed.get(f"/api/custom-fields/by-object/part/{part_id}").json()["data"]
    by_key = {r["key"]: r for r in rows}
    assert by_key["Resistance"]["source"] == "provider"
    assert by_key["image_url"]["source"] == "provider"
    assert by_key["datasheet_url"]["source"] == "provider"


def test_refresh_requires_mpn(authed, monkeypatch):
    _enable_mouser(authed)
    part_id = _create_part(authed, "Local", mpn=None)
    r = authed.post(f"/api/parts/{part_id}/refresh-from-provider")
    assert r.status_code == 400
    assert "MPN" in r.json()["status"]["message"]


def test_refresh_requires_provider(authed):
    part_id = _create_part(authed, "Resistor", "X")
    r = authed.post(f"/api/parts/{part_id}/refresh-from-provider")
    assert r.status_code == 400
    assert "no parts provider" in r.json()["status"]["message"].lower()


def test_refresh_preserves_overrides_manuals_and_localedit(authed, monkeypatch):
    _enable_mouser(authed)
    part_id = _create_part(authed, "Resistor", "RC0402JR-070R")

    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_response(),
    )
    authed.post(f"/api/parts/{part_id}/refresh-from-provider")

    # Override the Resistance row (source becomes 'override').
    rows = authed.get(f"/api/custom-fields/by-object/part/{part_id}").json()["data"]
    res_row = next(r for r in rows if r["key"] == "Resistance")
    authed.post(
        "/api/custom-fields",
        json={
            "object_type": "part",
            "object_id": part_id,
            "key": "Resistance",
            "value": "0.0 Ohms (verified)",
        },
    )
    rows = authed.get(f"/api/custom-fields/by-object/part/{part_id}").json()["data"]
    res_row = next(r for r in rows if r["key"] == "Resistance")
    assert res_row["source"] == "override"
    assert res_row["original_value"] == "0 Ohms"

    # Add a manual spec.
    authed.post(
        "/api/custom-fields",
        json={
            "object_type": "part",
            "object_id": part_id,
            "key": "Internal QA",
            "value": "Approved",
        },
    )

    # Locally edit the description (should flip the locally-edited flag).
    authed.patch(f"/api/parts/{part_id}", json={"description": "MY EDIT"})

    # Now refresh again; nothing manual or override should be lost,
    # description must not be overwritten.
    authed.post(f"/api/parts/{part_id}/refresh-from-provider")

    rows = authed.get(f"/api/custom-fields/by-object/part/{part_id}").json()["data"]
    by_key = {r["key"]: r for r in rows}
    assert by_key["Resistance"]["source"] == "override"
    assert by_key["Resistance"]["value"] == "0.0 Ohms (verified)"
    # original_value is the *latest* upstream value (so Restore reflects current upstream)
    assert by_key["Resistance"]["original_value"] == "0 Ohms"
    assert by_key["Internal QA"]["source"] == "manual"
    p = authed.get(f"/api/parts/{part_id}").json()["data"]
    assert p["description"] == "MY EDIT"
    assert p["description_locally_edited"] is True


def test_refresh_drops_stale_provider_rows(authed, monkeypatch):
    _enable_mouser(authed)
    part_id = _create_part(authed, "Resistor", "RC0402JR-070R")

    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_response(),
    )
    authed.post(f"/api/parts/{part_id}/refresh-from-provider")
    rows = authed.get(f"/api/custom-fields/by-object/part/{part_id}").json()["data"]
    keys_before = {r["key"] for r in rows}
    assert "Tolerance" in keys_before

    # Upstream stops returning Tolerance.
    leaner = dict(_FAKE_PART)
    leaner["ProductAttributes"] = [
        {"AttributeName": "Resistance", "AttributeValue": "0 Ohms"},
    ]
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_response(leaner),
    )
    authed.post(f"/api/parts/{part_id}/refresh-from-provider")
    rows = authed.get(f"/api/custom-fields/by-object/part/{part_id}").json()["data"]
    keys_after = {r["key"] for r in rows}
    assert "Tolerance" not in keys_after
    assert "Resistance" in keys_after


# ---------------------------------------------------------------------------
# override / restore semantics on plain custom_fields POST/DELETE
# ---------------------------------------------------------------------------


def test_manual_create_defaults_to_manual_source(authed):
    part_id = _create_part(authed, "Local")
    authed.post(
        "/api/custom-fields",
        json={"object_type": "part", "object_id": part_id, "key": "Note", "value": "hi"},
    )
    rows = authed.get(f"/api/custom-fields/by-object/part/{part_id}").json()["data"]
    assert rows[0]["source"] == "manual"


def test_restore_override_endpoint(authed, monkeypatch):
    _enable_mouser(authed)
    part_id = _create_part(authed, "R", "RC0402JR-070R")
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_response(),
    )
    authed.post(f"/api/parts/{part_id}/refresh-from-provider")

    # Override Tolerance.
    authed.post(
        "/api/custom-fields",
        json={
            "object_type": "part",
            "object_id": part_id,
            "key": "Tolerance",
            "value": "1 %",
        },
    )
    rows = authed.get(f"/api/custom-fields/by-object/part/{part_id}").json()["data"]
    tol = next(r for r in rows if r["key"] == "Tolerance")
    assert tol["source"] == "override"
    assert tol["original_value"] == "5 %"

    # Restore.
    r = authed.delete(f"/api/custom-fields/{tol['id']}/override")
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["source"] == "provider"
    assert body["value"] == "5 %"
    assert body["original_value"] is None


def test_restore_endpoint_rejects_non_override(authed):
    part_id = _create_part(authed, "Local")
    authed.post(
        "/api/custom-fields",
        json={"object_type": "part", "object_id": part_id, "key": "k", "value": "v"},
    )
    rows = authed.get(f"/api/custom-fields/by-object/part/{part_id}").json()["data"]
    r = authed.delete(f"/api/custom-fields/{rows[0]['id']}/override")
    assert r.status_code == 400


def test_unlink_provider_converts_rows_and_clears_metadata(authed, monkeypatch):
    _enable_mouser(authed)
    part_id = _create_part(authed, "R", "RC0402JR-070R")
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_response(),
    )
    authed.post(f"/api/parts/{part_id}/refresh-from-provider")
    # Override one row so we can verify it loses original_value too.
    authed.post(
        "/api/custom-fields",
        json={"object_type": "part", "object_id": part_id, "key": "Tolerance", "value": "1 %"},
    )

    r = authed.patch(f"/api/parts/{part_id}", json={"unlink_provider": True})
    assert r.status_code == 200, r.text
    p = r.json()["data"]
    assert p["linked_provider"] is None
    assert p["last_refresh_at"] is None
    assert p["description_locally_edited"] is False

    rows = authed.get(f"/api/custom-fields/by-object/part/{part_id}").json()["data"]
    for row in rows:
        assert row["source"] == "manual"
        assert row["original_value"] is None


def test_linked_part_blocks_manufacturer_edit(authed, monkeypatch):
    _enable_mouser(authed)
    part_id = _create_part(authed, "R", "RC0402JR-070R")
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_response(),
    )
    authed.post(f"/api/parts/{part_id}/refresh-from-provider")

    r = authed.patch(f"/api/parts/{part_id}", json={"manufacturer": "Some Other Co"})
    assert r.status_code == 400
    assert "provider-owned" in r.json()["status"]["message"]

    # Same edit goes through after explicit unlink.
    r = authed.patch(
        f"/api/parts/{part_id}",
        json={"unlink_provider": True, "manufacturer": "Some Other Co"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["manufacturer"] == "Some Other Co"
