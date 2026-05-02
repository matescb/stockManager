"""Credential rotation audit row must NOT contain the API key text (BE2-024).

Regression guard: PATCH /api/workspaces/current with a new api_key must
produce an audit row whose ``comment`` contains only the field name(s),
never the plaintext secret value.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app

_SECRET_VALUE = "VERY-SECRET-API-KEY-12345"


def _signup(c: TestClient) -> None:
    r = c.post(
        "/api/auth/signup",
        json={
            "email": f"u-{uuid.uuid4().hex[:8]}@x.com",
            "name": "u",
            "password": "TestPass-2026-Stronk",
        },
    )
    assert r.status_code == 200, r.text


def test_credential_rotation_audit_no_secret():
    """Rotate a parts_provider_api_key; confirm the audit row does NOT
    store the plaintext key value anywhere in the row."""
    c = TestClient(app)
    _signup(c)

    # Set a recognisable secret value so we can search for it in the audit.
    r = c.patch(
        "/api/workspaces/current",
        json={
            "parts_provider": "mouser",
            "parts_provider_api_key": _SECRET_VALUE,
        },
    )
    assert r.status_code == 200, r.text

    # Read the audit log.
    r_audit = c.get("/api/audit")
    assert r_audit.status_code == 200, r_audit.text
    rows = r_audit.json()["data"]

    rotation_rows = [row for row in rows if row["action"] == "workspace.credentials_rotated"]
    assert len(rotation_rows) >= 1, "Expected a workspace.credentials_rotated audit row"

    # Stringify the whole row and assert the secret is absent.
    for row in rotation_rows:
        row_text = str(row)
        assert _SECRET_VALUE not in row_text, (
            f"Plaintext secret found in audit row: {row}"
        )

    # The comment should name the field but not the value.
    latest = rotation_rows[0]
    assert "parts_provider_api_key" in (latest.get("comment") or "")
