from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.secrets import decrypt
from app.domain.audit.models import AuditLog
from app.domain.workspaces.models import Workspace

VALID_LANGUAGE_CODES = (
    "de",
    "en",
    "es",
    "fr",
    "it",
    "pt",
    "ja",
    "zh-hans",
    "zh-hant",
)


def _current_workspace_id(authed_client) -> UUID:
    r = authed_client.get("/api/workspaces/current")
    assert r.status_code == 200, r.text
    return UUID(r.json()["data"]["id"])


def test_set_sourcing_credentials_persists_ciphertext(authed_client, db):
    company_id = "trustedparts-company-123"
    api_key = "trustedparts-api-key-456"

    r = authed_client.patch(
        "/api/workspaces/current",
        json={
            "sourcing_provider": "trustedparts",
            "sourcing_company_id": company_id,
            "sourcing_api_key": api_key,
            "sourcing_country_code": "CZ",
            "sourcing_currency_code": "EUR",
            "sourcing_preferred_distributors": ["DigiKey", "Mouser"],
            "sourcing_use_cached_for_dashboards": False,
        },
    )
    assert r.status_code == 200, r.text

    ws = db.get(Workspace, _current_workspace_id(authed_client))
    assert ws is not None
    assert ws.sourcing_provider == "trustedparts"
    assert ws.sourcing_country_code == "CZ"
    assert ws.sourcing_currency_code == "EUR"
    assert ws.sourcing_preferred_distributors == ["DigiKey", "Mouser"]
    assert ws.sourcing_use_cached_for_dashboards is False
    assert ws.sourcing_company_id_enc is not None
    assert ws.sourcing_company_id_enc != company_id
    assert decrypt(ws.sourcing_company_id_enc) == company_id
    assert ws.sourcing_api_key_enc is not None
    assert ws.sourcing_api_key_enc != api_key
    assert decrypt(ws.sourcing_api_key_enc) == api_key


def test_default_language_code_is_null(authed_client, db):
    r = authed_client.get("/api/workspaces/current")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["sourcing_language_code"] is None

    ws = db.get(Workspace, _current_workspace_id(authed_client))
    assert ws is not None
    assert ws.sourcing_language_code is None


@pytest.mark.parametrize("language_code", VALID_LANGUAGE_CODES)
def test_patch_valid_language_code_persists(authed_client, db, language_code):
    r = authed_client.patch(
        "/api/workspaces/current",
        json={"sourcing_language_code": language_code},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["sourcing_language_code"] == language_code

    ws = db.get(Workspace, _current_workspace_id(authed_client))
    assert ws is not None
    assert ws.sourcing_language_code == language_code


def test_patch_invalid_language_code_returns_422(authed_client):
    r = authed_client.patch(
        "/api/workspaces/current",
        json={"sourcing_language_code": "klingon"},
    )

    assert r.status_code == 422
    body = r.json()
    assert body["data"] is None
    assert body["status"]["category"] == "validation_error"
    assert any(
        error["field"] == "body.sourcing_language_code"
        for error in body.get("errors", [])
    )


def test_clear_sourcing_credential_with_empty_string(authed_client, db):
    r = authed_client.patch(
        "/api/workspaces/current",
        json={
            "sourcing_provider": "trustedparts",
            "sourcing_company_id": "company-to-clear",
            "sourcing_api_key": "api-key-to-clear",
        },
    )
    assert r.status_code == 200, r.text

    r = authed_client.patch(
        "/api/workspaces/current",
        json={"sourcing_company_id": "", "sourcing_api_key": "   "},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["has_sourcing_company_id"] is False
    assert r.json()["data"]["has_sourcing_api_key"] is False

    ws = db.get(Workspace, _current_workspace_id(authed_client))
    assert ws is not None
    assert ws.sourcing_company_id_enc is None
    assert ws.sourcing_api_key_enc is None


def test_invalid_sourcing_provider_returns_422_envelope(authed_client):
    r = authed_client.patch(
        "/api/workspaces/current",
        json={"sourcing_provider": "bogus"},
    )

    assert r.status_code == 422
    body = r.json()
    assert body["data"] is None
    assert body["status"]["category"] == "validation_error"
    assert body["status"]["message"] == "validation failed"
    assert any(
        error["field"] == "body.sourcing_provider"
        for error in body.get("errors", [])
    )


def test_audit_log_records_field_names_not_values(authed_client, db):
    company_id = "audit-company-plaintext"
    api_key = "audit-api-key-plaintext"

    r = authed_client.patch(
        "/api/workspaces/current",
        json={
            "sourcing_provider": "trustedparts",
            "sourcing_company_id": company_id,
            "sourcing_api_key": api_key,
            "sourcing_country_code": "CZ",
            "sourcing_currency_code": "EUR",
        },
    )
    assert r.status_code == 200, r.text

    rows = db.execute(
        select(AuditLog)
        .where(AuditLog.action == "workspace.credentials_rotated")
        .order_by(AuditLog.created_at.desc())
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.comment == "fields=sourcing_company_id,sourcing_api_key"
    row_text = str(
        {
            "action": row.action,
            "target_type": row.target_type,
            "target_ids": row.target_ids,
            "comment": row.comment,
        }
    )
    assert company_id not in row_text
    assert api_key not in row_text


def test_get_after_patch_masks_creds(authed_client):
    company_id = "masked-company-id"
    api_key = "masked-api-key"

    r = authed_client.patch(
        "/api/workspaces/current",
        json={
            "sourcing_provider": "trustedparts",
            "sourcing_company_id": company_id,
            "sourcing_api_key": api_key,
        },
    )
    assert r.status_code == 200, r.text

    r = authed_client.get("/api/workspaces/current")
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["sourcing_provider"] == "trustedparts"
    assert body["has_sourcing_company_id"] is True
    assert body["has_sourcing_api_key"] is True
    assert "sourcing_company_id" not in body
    assert "sourcing_api_key" not in body
    assert "sourcing_company_id_enc" not in body
    assert "sourcing_api_key_enc" not in body
    assert company_id not in r.text
    assert api_key not in r.text
