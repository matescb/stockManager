from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.core.ratelimit as _ratelimit_mod
from app.domain.sourcing.budget import BUDGET
from app.domain.sourcing.schemas import (
    SourcingDistributor,
    SourcingLinks,
    SourcingOffer,
    SourcingQuery,
    SourcingSearchRaw,
)
from app.main import app
from tests._factories import add_stock, create_part, create_project_with_bom, signup_user


class _FakeTrustedPartsClient:
    calls: list[dict[str, Any]] = []
    offers_by_mpn: dict[str, list[SourcingOffer]] = {}

    def __init__(self, workspace_id: uuid.UUID) -> None:
        self.workspace_id = workspace_id
        self.country_code = "US"
        self.currency_code = "USD"

    def search(
        self,
        queries: list[SourcingQuery],
        *,
        in_stock_only: bool,
        distributors: list[str] | None,
        use_cached_data: bool,
        **_kwargs: Any,
    ) -> SourcingSearchRaw:
        mpn = queries[0].search_token
        self.calls.append(
            {
                "workspace_id": str(self.workspace_id),
                "mpn": mpn,
                "use_cached_data": use_cached_data,
                "in_stock_only": in_stock_only,
                "distributors": distributors,
            }
        )
        return SourcingSearchRaw(
            offers=self.offers_by_mpn.get(mpn, [_offer(mpn)]),
            request_id=f"req-{len(self.calls)}",
        )


def _offer(mpn: str, *, stock: int = 100, unit_price: float = 1.0) -> SourcingOffer:
    return SourcingOffer(
        mpn=mpn,
        manufacturer="TestCo",
        description=f"Offer for {mpn}",
        distributors=[
            SourcingDistributor(
                name="DigiKey",
                stock=stock,
                unit_price=unit_price,
                currency="USD",
                moq=1,
                price_breaks=[],
                product_url=f"https://www.trustedparts.com/{mpn}",
            )
        ],
        links=SourcingLinks(primary=f"https://www.trustedparts.com/search/{mpn}"),
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


def _configure_sourcing(client: TestClient, *, use_cached_for_dashboards: bool = False) -> None:
    r = client.patch(
        "/api/workspaces/current",
        json={
            "sourcing_provider": "trustedparts",
            "sourcing_company_id": "company-123",
            "sourcing_api_key": "api-key-456",
            "sourcing_country_code": "US",
            "sourcing_currency_code": "USD",
            "sourcing_preferred_distributors": ["DigiKey"],
            "sourcing_use_cached_for_dashboards": use_cached_for_dashboards,
        },
    )
    assert r.status_code == 200, r.text


def _workspace_id(client: TestClient) -> uuid.UUID:
    r = client.get("/api/workspaces/current")
    assert r.status_code == 200, r.text
    return uuid.UUID(r.json()["data"]["id"])


def _project(client: TestClient, name: str, *, mpn: str, quantity: int = 10) -> str:
    part_id = create_part(client, name=f"Part {mpn}", mpn=mpn)
    return create_project_with_bom(
        client,
        name,
        [{"part_id": part_id, "quantity": quantity}],
    )


def test_returns_one_row_per_project(authed_client):
    _configure_sourcing(authed_client)
    first = _project(authed_client, "Alpha", mpn="ALPHA", quantity=10)
    second = _project(authed_client, "Beta", mpn="BETA", quantity=5)
    _FakeTrustedPartsClient.offers_by_mpn = {
        "ALPHA": [_offer("ALPHA", stock=20, unit_price=0.5)],
        "BETA": [_offer("BETA", stock=3, unit_price=2.0)],
    }

    r = authed_client.get("/api/reports/bom-buyability?build_quantity=2")

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["sourcing_status"] == "ok"
    assert data["truncated"] is False
    rows = {row["project_id"]: row for row in data["rows"]}
    assert set(rows) == {first, second}
    assert rows[first]["build_quantity"] == 2
    assert rows[first]["can_build_now"] == 0
    assert rows[first]["can_build_after_purchase"] == 2
    assert rows[first]["est_purchase_cost"] == "10.0"
    assert rows[second]["can_build_after_purchase"] == 0
    assert rows[second]["blocking_lines_count"] == 1


def test_50_project_cap_with_truncated_flag(authed_client):
    _configure_sourcing(authed_client)
    for index in range(51):
        _project(authed_client, f"Project {index:02d}", mpn=f"CAP-{index:02d}", quantity=1)

    r = authed_client.get("/api/reports/bom-buyability")

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["truncated"] is True
    assert data["project_cap"] == 50
    assert len(data["rows"]) == 50


def test_invalid_build_quantity_returns_422(authed_client):
    r = authed_client.get("/api/reports/bom-buyability?build_quantity=0")

    assert r.status_code == 422, r.text
    assert r.json()["status"]["category"] == "validation_error"


def test_not_configured_status_flag(authed_client, monkeypatch):
    monkeypatch.setattr(
        "app.domain.sourcing.service.make_sourcing_provider",
        lambda _workspace: None,
    )
    _project(authed_client, "Unconfigured", mpn="NO-CONFIG", quantity=10)

    r = authed_client.get("/api/reports/bom-buyability")

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["sourcing_status"] == "not_configured"
    assert data["rows"][0]["partial"] is True
    assert data["rows"][0]["can_build_after_purchase"] == data["rows"][0]["can_build_now"]
    assert data["rows"][0]["est_purchase_cost"] is None


def test_budget_blocked_status_flag(authed_client):
    _configure_sourcing(authed_client)
    _project(authed_client, "Blocked", mpn="BUDGET", quantity=10)
    BUDGET.record(_workspace_id(authed_client), 250)

    r = authed_client.get("/api/reports/bom-buyability")

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["sourcing_status"] == "budget_blocked"
    assert data["rows"][0]["partial"] is True
    assert _FakeTrustedPartsClient.calls == []


def test_workspace_isolation():
    a = TestClient(app)
    b = TestClient(app)
    signup_user(a, email=f"a-{uuid.uuid4().hex[:8]}@x.com")
    signup_user(b, email=f"b-{uuid.uuid4().hex[:8]}@x.com")
    _configure_sourcing(a)
    _configure_sourcing(b)
    project_a = _project(a, "A Project", mpn="TENANT-A", quantity=1)
    project_b = _project(b, "B Project", mpn="TENANT-B", quantity=1)

    r = a.get("/api/reports/bom-buyability")

    assert r.status_code == 200, r.text
    ids = {row["project_id"] for row in r.json()["data"]["rows"]}
    assert project_a in ids
    assert project_b not in ids


def test_cache_hit_within_4h(authed_client):
    _configure_sourcing(authed_client, use_cached_for_dashboards=False)
    part_id = create_part(authed_client, name="Cache Part", mpn="CACHE-MPN")
    add_stock(authed_client, part_id, 1)
    create_project_with_bom(
        authed_client,
        "Cache Project",
        [{"part_id": part_id, "quantity": 5}],
    )

    first = authed_client.get("/api/reports/bom-buyability")
    second = authed_client.get("/api/reports/bom-buyability")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert len(_FakeTrustedPartsClient.calls) == 1
    assert _FakeTrustedPartsClient.calls[0]["use_cached_data"] is True
