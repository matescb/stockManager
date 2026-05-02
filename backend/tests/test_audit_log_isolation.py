"""Workspace isolation for the audit endpoint (BE2-024).

Admin in workspace A cannot read workspace B's audit log.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


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


def _create_part(c: TestClient, name: str) -> str:
    r = c.post("/api/parts", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def test_audit_log_workspace_isolation():
    """Workspace A's audit log is not visible to workspace B's admin."""
    a = TestClient(app)
    b = TestClient(app)
    _signup(a)
    _signup(b)

    # A does a bulk-delete — generates an audit row in workspace A.
    pid = _create_part(a, "PartA")
    r = a.post("/api/parts/bulk-delete", json={"part_ids": [pid]})
    assert r.status_code == 200, r.text

    # B reads their own audit log — must be empty (no A rows visible).
    r_b = b.get("/api/audit")
    assert r_b.status_code == 200, r_b.text
    rows_b = r_b.json()["data"]
    # None of A's action rows should appear in B's list.
    assert all(row["action"] != "part.bulk_archived" for row in rows_b), (
        "Cross-workspace audit rows leaked into workspace B's log"
    )

    # A reads their own log — must contain the row.
    r_a = a.get("/api/audit")
    assert r_a.status_code == 200, r_a.text
    actions_a = [row["action"] for row in r_a.json()["data"]]
    assert "part.bulk_archived" in actions_a
