from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.audit.models import AuditLog
from app.domain.workspaces.master_lists import (
    DEFAULT_ACTIVE_COUNTRIES,
    DEFAULT_ACTIVE_CURRENCIES,
    DEFAULT_ACTIVE_DISTRIBUTORS,
)
from app.domain.workspaces.models import Workspace
from app.main import app
from tests._factories import signup_user


def _load_backfill_migration():
    path = (
        Path(__file__).resolve().parent.parent
        / "alembic"
        / "versions"
        / "0045_backfill_active_distributors.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0045", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _current_workspace(client: TestClient) -> dict:
    r = client.get("/api/workspaces/current")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_default_active_lists(authed_client):
    body = _current_workspace(authed_client)

    assert body["active_currencies"] == DEFAULT_ACTIVE_CURRENCIES
    assert body["active_countries"] == DEFAULT_ACTIVE_COUNTRIES
    assert body["active_distributors"] == DEFAULT_ACTIVE_DISTRIBUTORS


def test_patch_replaces_active_currencies(authed_client, db):
    r = authed_client.patch(
        "/api/workspaces/current",
        json={
            "active_currencies": ["EUR", "JPY"],
            "active_countries": ["CZ", "JP"],
            "active_distributors": ["DigiKey", "RS Components"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["active_currencies"] == ["EUR", "JPY"]
    assert body["active_countries"] == ["CZ", "JP"]
    assert body["active_distributors"] == ["DigiKey", "RS Components"]

    ws = db.get(Workspace, UUID(body["id"]))
    assert ws is not None
    assert ws.active_currencies == ["EUR", "JPY"]
    assert ws.active_countries == ["CZ", "JP"]
    assert ws.active_distributors == ["DigiKey", "RS Components"]

    row = db.execute(
        select(AuditLog).where(AuditLog.action == "workspace.active_lists_updated")
    ).scalar_one()
    assert "active_currencies" in (row.comment or "")
    assert "active_countries" in (row.comment or "")
    assert "active_distributors" in (row.comment or "")


def test_empty_currencies_returns_422(authed_client):
    r = authed_client.patch(
        "/api/workspaces/current",
        json={"active_currencies": []},
    )

    assert r.status_code == 422
    body = r.json()
    assert body["data"] is None
    assert body["status"]["category"] == "validation_error"
    assert any(error["field"] == "body.active_currencies" for error in body["errors"])


def test_invalid_iso_currency_code_returns_422(authed_client):
    r = authed_client.patch(
        "/api/workspaces/current",
        json={"active_currencies": ["EUR", "usd"]},
    )

    assert r.status_code == 422
    body = r.json()
    assert body["data"] is None
    assert body["status"]["category"] == "validation_error"
    assert any(
        error["field"] == "body.active_currencies.1" for error in body["errors"]
    )


def test_master_lists_endpoint(authed_client):
    r = authed_client.get("/api/workspaces/master-lists")
    assert r.status_code == 200, r.text

    body = r.json()["data"]
    assert set(DEFAULT_ACTIVE_CURRENCIES).issubset(body["currencies"])
    assert set(DEFAULT_ACTIVE_COUNTRIES).issubset(body["countries"])
    assert set(DEFAULT_ACTIVE_DISTRIBUTORS).issubset(body["distributors"])
    assert "JPY" in body["currencies"]
    assert "JP" in body["countries"]
    assert "RS Components" in body["distributors"]


def test_workspace_isolation_active_lists(db):
    client_a = TestClient(app)
    client_b = TestClient(app)
    signup_user(client_a, email="active-a@example.com")
    signup_user(client_b, email="active-b@example.com")

    ws_a = _current_workspace(client_a)
    ws_b = _current_workspace(client_b)
    assert ws_a["id"] != ws_b["id"]

    r = client_a.patch(
        "/api/workspaces/current",
        json={"active_currencies": ["EUR"], "active_countries": ["CZ"]},
    )
    assert r.status_code == 200, r.text

    a_after = _current_workspace(client_a)
    b_after = _current_workspace(client_b)
    assert a_after["active_currencies"] == ["EUR"]
    assert a_after["active_countries"] == ["CZ"]
    assert b_after["active_currencies"] == DEFAULT_ACTIVE_CURRENCIES
    assert b_after["active_countries"] == DEFAULT_ACTIVE_COUNTRIES

    db_ws_b = db.get(Workspace, UUID(ws_b["id"]))
    assert db_ws_b is not None
    assert db_ws_b.active_currencies == DEFAULT_ACTIVE_CURRENCIES


def test_migration_backfills_active_lists_with_saved_sourcing_defaults(authed_client, db):
    body = _current_workspace(authed_client)
    ws = db.get(Workspace, UUID(body["id"]))
    assert ws is not None
    ws.active_distributors = ["DigiKey", "Mouser"]
    ws.sourcing_preferred_distributors = ["Arrow", "Newark"]
    ws.active_countries = ["CZ", "DE"]
    ws.sourcing_country_code = "US"
    ws.active_currencies = ["EUR", "CZK"]
    ws.sourcing_currency_code = "USD"
    db.flush()

    migration = _load_backfill_migration()
    migration.backfill_active_lists(db.connection())
    migration.backfill_active_lists(db.connection())
    db.expire(ws)

    assert set(ws.active_distributors) == {"DigiKey", "Mouser", "Arrow", "Newark"}
    assert len(ws.active_distributors) == 4
    assert set(ws.active_countries) == {"CZ", "DE", "US"}
    assert len(ws.active_countries) == 3
    assert set(ws.active_currencies) == {"EUR", "CZK", "USD"}
    assert len(ws.active_currencies) == 3
