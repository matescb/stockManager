from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.core.ratelimit as _ratelimit_mod
from app.core.time import utcnow
from app.domain.projects.models import Project
from app.domain.sourcing.budget import BUDGET
from app.domain.sourcing.models import PurchasePlan, PurchasePlanLine
from app.main import app
from tests._factories import signup_user
from tests.test_purchase_plan_route import (
    _configure_sourcing,
    _FakeTrustedPartsClient,
    _offer,
    _post_plan,
    _single_line_project,
)


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.fixture(autouse=True)
def reset_sourcing_state(monkeypatch):
    original_limiter_enabled = _ratelimit_mod.limiter.enabled
    _ratelimit_mod.limiter.enabled = False
    _FakeTrustedPartsClient.calls = []
    _FakeTrustedPartsClient.offers_by_mpn = {}
    BUDGET._events.clear()
    try:
        _ratelimit_mod.limiter.reset()
    except Exception:
        pass
    monkeypatch.setattr(
        "app.domain.sourcing.service.make_sourcing_provider",
        lambda workspace: _FakeTrustedPartsClient(workspace.id),
    )
    yield
    _ratelimit_mod.limiter.enabled = original_limiter_enabled
    BUDGET._events.clear()
    try:
        _ratelimit_mod.limiter.reset()
    except Exception:
        pass


def _refresh(client: TestClient, plan_id: str, body: dict | None = None):
    return client.post(
        f"/api/sourcing/purchase-plans/{plan_id}/refresh",
        json=body if body is not None else None,
    )


def _create_plan(client: TestClient, *, mpn: str = "REFRESH", strategy: str = "preferred_first"):
    _configure_sourcing(client, preferred=["DigiKey"])
    project_id = _single_line_project(client, mpn=mpn, quantity=5)
    r = _post_plan(client, project_id, strategy=strategy)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_refresh_replaces_lines_and_sets_status(authed_client, db):
    _FakeTrustedPartsClient.offers_by_mpn = {
        "REFRESH": [_offer("REFRESH", distributor="DigiKey", stock=50, unit_price=1.0)]
    }
    initial = _create_plan(authed_client)
    original_line_id = initial["lines"][0]["id"]
    original_expires_at = initial["expires_at"]
    _FakeTrustedPartsClient.offers_by_mpn = {
        "REFRESH": [_offer("REFRESH", distributor="Mouser", stock=50, unit_price=0.5)]
    }

    r = _refresh(authed_client, initial["id"])

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "refreshed"
    assert data["last_refreshed_at"] is not None
    assert _parse_iso(data["expires_at"]) == _parse_iso(original_expires_at)
    assert len(data["lines"]) == 1
    assert data["lines"][0]["id"] != original_line_id
    assert data["lines"][0]["selected_distributor"] == "Mouser"

    rows = db.execute(
        select(PurchasePlanLine).where(PurchasePlanLine.purchase_plan_id == uuid.UUID(data["id"]))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].selected_distributor == "Mouser"


def test_refresh_does_not_extend_expires_at(authed_client, db):
    initial = _create_plan(authed_client)
    before = db.get(PurchasePlan, uuid.UUID(initial["id"]))
    assert before is not None
    expires_at = before.expires_at

    r = _refresh(authed_client, initial["id"])

    assert r.status_code == 200, r.text
    after = db.get(PurchasePlan, uuid.UUID(initial["id"]))
    assert after is not None
    assert after.expires_at == expires_at


def test_refresh_uses_persisted_strategy_not_request_body(authed_client):
    _FakeTrustedPartsClient.offers_by_mpn = {
        "STRATEGY": [
            _offer(
                "STRATEGY",
                distributor="SlowCheap",
                stock=50,
                unit_price=0.25,
                lead_time_days=20,
            ),
            _offer(
                "STRATEGY",
                distributor="FastCostly",
                stock=50,
                unit_price=9.0,
                lead_time_days=2,
            ),
        ]
    }
    initial = _create_plan(
        authed_client,
        mpn="STRATEGY",
        strategy="fastest_availability",
    )

    r = _refresh(authed_client, initial["id"], body={"strategy": "lowest_total_price"})

    assert r.status_code == 200, r.text
    assert r.json()["data"]["lines"][0]["selected_distributor"] == "FastCostly"


def test_foreign_plan_returns_404(authed_client):
    initial = _create_plan(authed_client)

    client_b = TestClient(app)
    signup_user(client_b)
    _configure_sourcing(client_b)

    r = _refresh(client_b, initial["id"])

    assert r.status_code == 404, r.text
    assert r.json()["code"] == "resource.not_found"
    assert r.json()["status"]["category"] == "not_found"


def test_expired_plan_returns_conflict(authed_client, db):
    initial = _create_plan(authed_client)
    plan = db.get(PurchasePlan, uuid.UUID(initial["id"]))
    assert plan is not None
    plan.expires_at = utcnow() - timedelta(minutes=1)
    db.flush()

    r = _refresh(authed_client, initial["id"])

    assert r.status_code == 409, r.text
    assert r.json()["code"] == "sourcing.plan_expired"
    assert r.json()["status"] == {"category": "conflict", "message": "plan expired"}


def test_workspace_isolation_two_plans_different_workspaces(authed_client):
    plan_a = _create_plan(authed_client, mpn="WS-A")

    client_b = TestClient(app)
    signup_user(client_b)
    plan_b = _create_plan(client_b, mpn="WS-B")

    foreign = _refresh(client_b, plan_a["id"])
    own = _refresh(client_b, plan_b["id"])

    assert foreign.status_code == 404, foreign.text
    assert foreign.json()["code"] == "resource.not_found"
    assert own.status_code == 200, own.text
    assert own.json()["data"]["id"] == plan_b["id"]


def test_refresh_rejects_archived_project(authed_client, db):
    initial = _create_plan(authed_client)
    plan = db.get(PurchasePlan, uuid.UUID(initial["id"]))
    assert plan is not None
    project = db.get(Project, plan.project_id)
    assert project is not None
    project.archived_at = utcnow()
    db.flush()

    r = _refresh(authed_client, initial["id"])

    assert r.status_code == 404, r.text
    assert r.json()["code"] == "project.not_found"


def test_decimals_as_strings_on_wire(authed_client):
    _FakeTrustedPartsClient.offers_by_mpn = {
        "DECIMAL": [_offer("DECIMAL", distributor="DigiKey", stock=50, unit_price=1.25)]
    }
    initial = _create_plan(authed_client, mpn="DECIMAL")

    r = _refresh(authed_client, initial["id"])

    assert r.status_code == 200, r.text
    unit_price = r.json()["data"]["lines"][0]["selected_unit_price"]
    total = r.json()["data"]["est_total_cost"]
    assert isinstance(unit_price, str)
    assert isinstance(total, str)
    assert Decimal(unit_price) == Decimal("1.25")
    assert Decimal(total) == Decimal("6.25")
