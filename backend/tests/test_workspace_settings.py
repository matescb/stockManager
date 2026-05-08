from __future__ import annotations

from uuid import UUID

from app.domain.workspaces.models import Workspace


def test_workspace_get_includes_sourcing_defaults(authed_client):
    r = authed_client.get("/api/workspaces/current")
    assert r.status_code == 200, r.text

    body = r.json()["data"]
    assert body["sourcing_provider"] == "none"
    assert body["sourcing_country_code"] is None
    assert body["sourcing_currency_code"] is None
    assert body["sourcing_preferred_distributors"] is None
    assert body["sourcing_use_cached_for_dashboards"] is True
    assert body["has_sourcing_company_id"] is False
    assert body["has_sourcing_api_key"] is False


def test_workspace_get_never_returns_sourcing_secrets(authed_client, db):
    cur = authed_client.get("/api/workspaces/current").json()["data"]
    ws = db.get(Workspace, UUID(cur["id"]))
    ws.sourcing_company_id_enc = "ciphertext-company-id"
    ws.sourcing_api_key_enc = "ciphertext-api-key"
    db.flush()

    r = authed_client.get("/api/workspaces/current")
    assert r.status_code == 200, r.text
    body = r.json()["data"]

    assert body["has_sourcing_company_id"] is True
    assert body["has_sourcing_api_key"] is True
    assert "sourcing_company_id" not in body
    assert "sourcing_api_key" not in body
    assert "sourcing_company_id_enc" not in body
    assert "sourcing_api_key_enc" not in body
    assert "plaintext-company-id" not in r.text
    assert "plaintext-api-key" not in r.text
    assert "ciphertext-company-id" not in r.text
    assert "ciphertext-api-key" not in r.text


def test_workspace_serializer_shape_unchanged_for_existing_fields(authed_client):
    r = authed_client.get("/api/workspaces/current")
    assert r.status_code == 200, r.text

    body = r.json()["data"]
    assert {
        "id",
        "name",
        "kind",
        "currency_default",
        "lot_control_enabled",
        "serial_tracking_enabled",
        "catalog_enabled",
        "catalog_token_set",
        "parts_provider",
        "has_parts_provider_api_key",
        "has_parts_provider_api_secret",
        "scanner",
        "has_scanner_license_key",
    }.issubset(body)
