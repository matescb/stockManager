from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.core.ratelimit as _ratelimit_mod
from app.core.time import utcnow
from app.domain.sourcing.budget import BUDGET
from app.domain.sourcing.models import PurchasePlan, PurchasePlanLine
from app.domain.sourcing.schemas import (
    SourcingDistributor,
    SourcingLinks,
    SourcingOffer,
    SourcingPriceBreak,
    SourcingQuery,
    SourcingSearchRaw,
)
from app.main import app
from tests._factories import create_part, create_project_with_bom, signup_user


def _configure_sourcing(
    client: TestClient,
    *,
    preferred: list[str] | None = None,
) -> None:
    r = client.patch(
        "/api/workspaces/current",
        json={
            "sourcing_provider": "trustedparts",
            "sourcing_company_id": "company-123",
            "sourcing_api_key": "api-key-456",
            "sourcing_country_code": "CZ",
            "sourcing_currency_code": "EUR",
            "sourcing_preferred_distributors": preferred or ["DigiKey"],
            "sourcing_use_cached_for_dashboards": False,
        },
    )
    assert r.status_code == 200, r.text


def _current_workspace_id(client: TestClient) -> uuid.UUID:
    r = client.get("/api/workspaces/current")
    assert r.status_code == 200, r.text
    return uuid.UUID(r.json()["data"]["id"])


def _offer(
    mpn: str,
    *,
    distributor: str = "DigiKey",
    stock: int = 100,
    unit_price: float = 1.0,
    moq: int | None = 1,
    lead_time_days: int | None = 3,
    currency: str = "EUR",
) -> SourcingOffer:
    return SourcingOffer(
        mpn=mpn,
        manufacturer="TestCo",
        description=f"Offer for {mpn}",
        distributors=[
            SourcingDistributor(
                name=distributor,
                sku=f"{mpn}-{distributor}",
                stock=stock,
                unit_price=unit_price,
                currency=currency,
                moq=moq,
                lead_time_days=lead_time_days,
                price_breaks=[SourcingPriceBreak(quantity=1, unit_price=unit_price)],
                product_url=f"https://www.trustedparts.com/{mpn}/{distributor}",
            )
        ],
        links=SourcingLinks(primary=f"https://www.trustedparts.com/search/{mpn}"),
    )


class _FakeTrustedPartsClient:
    calls: list[dict[str, Any]] = []
    offers_by_mpn: dict[str, list[SourcingOffer]] = {}

    def __init__(self, workspace_id: uuid.UUID | None = None) -> None:
        self.workspace_id = workspace_id
        self.country_code = "CZ"
        self.currency_code = "EUR"

    def search(
        self,
        queries: list[SourcingQuery],
        *,
        in_stock_only: bool,
        distributors: list[str] | None,
        use_cached_data: bool,
        **_kwargs,
    ) -> SourcingSearchRaw:
        query = queries[0]
        self.calls.append(
            {
                "workspace_id": str(self.workspace_id) if self.workspace_id else None,
                "mpn": query.search_token,
                "distributors": distributors,
                "use_cached_data": use_cached_data,
            }
        )
        offers = self.offers_by_mpn.get(query.search_token)
        if offers is None and self.workspace_id is not None:
            distributor = f"WS-{str(self.workspace_id)[:8]}"
            offers = [_offer(query.search_token, distributor=distributor)]
        return SourcingSearchRaw(
            offers=offers or [],
            request_id=f"req-{len(self.calls)}",
        )


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


def _single_line_project(
    client: TestClient,
    *,
    mpn: str = "PLAN-MPN",
    quantity: int = 10,
) -> str:
    part_id = create_part(client, name=mpn, mpn=mpn)
    return create_project_with_bom(
        client,
        f"Plan Project {mpn}",
        [{"part_id": part_id, "quantity": quantity}],
    )


def _post_plan(
    client: TestClient,
    project_id: str,
    *,
    build_quantity: int = 1,
    strategy: str = "preferred_first",
):
    return client.post(
        f"/api/projects/{project_id}/purchase-plan",
        json={"build_quantity": build_quantity, "strategy": strategy},
    )


def test_basic_plan_creation_with_default_strategy(authed_client, db):
    _configure_sourcing(authed_client, preferred=["DigiKey"])
    project_id = _single_line_project(authed_client, mpn="BASIC", quantity=4)
    _FakeTrustedPartsClient.offers_by_mpn = {
        "BASIC": [
            _offer("BASIC", distributor="Mouser", stock=50, unit_price=1.0),
            _offer("BASIC", distributor="DigiKey", stock=50, unit_price=1.04),
        ]
    }

    r = authed_client.post(
        f"/api/projects/{project_id}/purchase-plan",
        json={"build_quantity": 2},
    )

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["strategy"] == "preferred_first"
    assert data["status"] == "draft"
    assert data["build_quantity"] == 2
    assert data["distributors_used"] == ["DigiKey"]
    assert data["unfilled_count"] == 0
    assert len(data["lines"]) == 1
    assert data["lines"][0]["selected_distributor"] == "DigiKey"
    assert Decimal(data["lines"][0]["selected_unit_price"]) == Decimal("1.04")

    plan = db.get(PurchasePlan, uuid.UUID(data["id"]))
    assert plan is not None
    assert plan.workspace_id == _current_workspace_id(authed_client)
    assert plan.expires_at <= plan.created_at + timedelta(days=7)


@pytest.mark.parametrize(
    "strategy",
    [
        "lowest_total_price",
        "fewest_distributors",
        "fastest_availability",
        "preferred_first",
    ],
)
def test_each_strategy_produces_a_persisted_plan(authed_client, db, strategy: str):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn=f"STRAT-{strategy}")

    r = _post_plan(authed_client, project_id, strategy=strategy)

    assert r.status_code == 200, r.text
    plan_id = uuid.UUID(r.json()["data"]["id"])
    rows = db.execute(
        select(PurchasePlanLine).where(PurchasePlanLine.purchase_plan_id == plan_id)
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].selected_distributor is not None


def test_foreign_project_returns_404(authed_client):
    other = TestClient(app)
    signup_user(other)
    _configure_sourcing(authed_client)
    foreign_project_id = _single_line_project(other, mpn="FOREIGN-MPN")

    r = _post_plan(authed_client, foreign_project_id)

    assert r.status_code == 404, r.text
    assert r.json()["status"]["category"] == "not_found"


def test_invalid_build_quantity_returns_422(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client)

    r = authed_client.post(
        f"/api/projects/{project_id}/purchase-plan",
        json={"build_quantity": 0},
    )

    assert r.status_code == 422, r.text
    assert r.json()["status"]["category"] == "validation_error"


def test_invalid_strategy_returns_422(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client)

    r = _post_plan(authed_client, project_id, strategy="magic")

    assert r.status_code == 422, r.text
    assert r.json()["status"]["category"] == "validation_error"


def test_unconfigured_returns_409(authed_client, monkeypatch):
    monkeypatch.setattr(
        "app.domain.sourcing.service.make_sourcing_provider",
        lambda _workspace: None,
    )
    project_id = _single_line_project(authed_client)

    r = _post_plan(authed_client, project_id)

    assert r.status_code == 409, r.text
    assert r.json()["status"] == {
        "category": "conflict",
        "message": "sourcing not configured",
    }


def test_budget_blocked_returns_503(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client)
    BUDGET.record(_current_workspace_id(authed_client), 250)

    r = _post_plan(authed_client, project_id)

    assert r.status_code == 503, r.text
    assert r.json()["status"] == {
        "category": "server_error",
        "message": "sourcing budget exhausted",
    }


def test_workspace_isolation_two_workspaces_same_project_id(authed_client, db):
    _configure_sourcing(authed_client)
    project_a = _single_line_project(authed_client, mpn="SHARED")

    client_b = TestClient(app)
    signup_user(client_b)
    _configure_sourcing(client_b)

    r = _post_plan(client_b, project_a)

    assert r.status_code == 404, r.text
    assert db.execute(select(PurchasePlan)).scalars().all() == []


def test_plan_lines_match_optimizer_output(authed_client, db):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="MATCH", quantity=3)
    _FakeTrustedPartsClient.offers_by_mpn = {
        "MATCH": [
            _offer("MATCH", distributor="Slow", stock=50, unit_price=0.5, lead_time_days=10),
            _offer("MATCH", distributor="Fast", stock=50, unit_price=1.5, lead_time_days=2),
        ]
    }

    r = _post_plan(authed_client, project_id, strategy="fastest_availability")

    assert r.status_code == 200, r.text
    line = r.json()["data"]["lines"][0]
    assert line["selected_distributor"] == "Fast"
    assert line["selected_qty"] == 3
    assert line["shortage_qty"] == 3
    assert Decimal(line["selected_unit_price"]) == Decimal("1.5")

    db_line = db.execute(select(PurchasePlanLine)).scalar_one()
    assert db_line.selected_distributor == line["selected_distributor"]
    assert db_line.selected_qty == line["selected_qty"]
    assert db_line.selected_unit_price == Decimal(line["selected_unit_price"])


def test_expires_at_uses_created_at_plus_seven_days(authed_client, db):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client)
    before = utcnow()

    r = _post_plan(authed_client, project_id)

    assert r.status_code == 200, r.text
    plan = db.get(PurchasePlan, uuid.UUID(r.json()["data"]["id"]))
    assert plan is not None
    assert plan.created_at >= before
    assert plan.expires_at == plan.created_at + timedelta(days=7)
