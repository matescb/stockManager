from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.core.ratelimit as _ratelimit_mod
from app.domain.reports.service import LOW_STOCK_SOURCING_TTL_SECONDS
from app.domain.sourcing.budget import BUDGET
from app.domain.sourcing.schemas import (
    SourcingDistributor,
    SourcingLinks,
    SourcingOffer,
    SourcingPriceBreak,
    SourcingQuery,
    SourcingSearchRaw,
)
from app.main import app
from tests._factories import add_stock, create_part, signup_user


def _configure_sourcing(
    client: TestClient,
    *,
    preferred: list[str] | None = None,
    use_cached_for_dashboards: bool = True,
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
            "sourcing_use_cached_for_dashboards": use_cached_for_dashboards,
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
        **_kwargs: Any,
    ) -> SourcingSearchRaw:
        query = queries[0]
        self.calls.append(
            {
                "workspace_id": str(self.workspace_id) if self.workspace_id else None,
                "mpn": query.search_token,
                "use_cached_data": use_cached_data,
                "distributors": distributors,
                "in_stock_only": in_stock_only,
            }
        )
        offers = self.offers_by_mpn.get(query.search_token)
        if offers is None and self.workspace_id is not None:
            offers = [
                _offer(
                    query.search_token,
                    distributor=f"WS-{str(self.workspace_id)[:8]}",
                )
            ]
        if offers is None:
            offers = [_offer(query.search_token)]
        return SourcingSearchRaw(offers=offers, request_id=f"req-{len(self.calls)}")


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


def _low_stock_part(
    client: TestClient,
    *,
    name: str = "Low",
    mpn: str | None = "LOW-MPN",
    threshold: int = 100,
    on_hand: int = 25,
) -> str:
    payload: dict[str, Any] = {
        "name": name,
        "low_stock_report_quantity": threshold,
    }
    if mpn is not None:
        payload["mpn"] = mpn
    part_id = create_part(client, **payload)
    if on_hand:
        add_stock(client, part_id, on_hand)
    return part_id


def test_include_sourcing_false_unchanged_from_baseline(authed_client):
    part_id = _low_stock_part(authed_client)

    r = authed_client.get("/api/reports/low-stock")

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert isinstance(data, list)
    assert data[0]["part_id"] == part_id
    assert "sourcing" not in data[0]
    assert "sourcing_status" not in data[0]
    assert _FakeTrustedPartsClient.calls == []


def test_include_sourcing_true_attaches_offers_for_parts_with_mpn(authed_client):
    _configure_sourcing(authed_client, preferred=["DigiKey"])
    part_id = _low_stock_part(authed_client, mpn="STM32")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "STM32": [_offer("STM32", stock=80, unit_price=0.25, moq=10, lead_time_days=5)]
    }

    r = authed_client.get("/api/reports/low-stock?include_sourcing=true")

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["sourcing_status"] == "ok"
    row = data["rows"][0]
    assert row["part_id"] == part_id
    assert row["sourcing"]["authorized_stock"] == 80
    assert row["sourcing"]["best_offer"]["distributor"] == "DigiKey"
    assert row["sourcing"]["best_offer"]["moq"] == 10
    assert row["sourcing"]["lead_time_days"] == 5
    assert row["sourcing"]["preferred_distributor_available"] is True
    assert row["sourcing"]["est_replenishment_cost"] == "18.75"
    assert _FakeTrustedPartsClient.calls[-1]["use_cached_data"] is True


def test_part_without_mpn_returns_null_sourcing(authed_client):
    _configure_sourcing(authed_client)
    _low_stock_part(authed_client, mpn=None)

    r = authed_client.get("/api/reports/low-stock?include_sourcing=true")

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["sourcing_status"] == "ok"
    assert data["rows"][0]["mpn"] is None
    assert data["rows"][0]["sourcing"] is None
    assert _FakeTrustedPartsClient.calls == []


def test_not_configured_returns_status_flag_not_409(authed_client, monkeypatch):
    monkeypatch.setattr(
        "app.domain.sourcing.service.make_sourcing_provider",
        lambda _workspace: None,
    )
    _low_stock_part(authed_client)

    r = authed_client.get("/api/reports/low-stock?include_sourcing=true")

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["sourcing_status"] == "not_configured"
    assert data["rows"][0]["sourcing"] is None


def test_budget_blocked_returns_status_flag_not_503(authed_client):
    _configure_sourcing(authed_client)
    _low_stock_part(authed_client)
    BUDGET.record(_current_workspace_id(authed_client), 250)

    r = authed_client.get("/api/reports/low-stock?include_sourcing=true")

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["sourcing_status"] == "budget_blocked"
    assert data["rows"][0]["sourcing"] is None


def test_4h_cache_ttl(authed_client, monkeypatch):
    _configure_sourcing(authed_client)
    _low_stock_part(authed_client, mpn="CACHE-MPN")
    ttl_values: list[int] = []
    original_search = __import__("app.domain.sourcing.service", fromlist=["search"]).search

    def spy_search(*args: Any, **kwargs: Any):
        ttl_values.append(kwargs["ttl_seconds"])
        return original_search(*args, **kwargs)

    monkeypatch.setattr("app.domain.sourcing.service.search", spy_search)

    first = authed_client.get("/api/reports/low-stock?include_sourcing=true")
    second = authed_client.get("/api/reports/low-stock?include_sourcing=true")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert ttl_values == [LOW_STOCK_SOURCING_TTL_SECONDS, LOW_STOCK_SOURCING_TTL_SECONDS]
    assert first.json()["data"]["rows"][0]["sourcing"]["cache_hit"] is False
    assert second.json()["data"]["rows"][0]["sourcing"]["cache_hit"] is True
    assert len(_FakeTrustedPartsClient.calls) == 1


def test_workspace_isolation_two_workspaces_same_part_mpn(authed_client):
    _configure_sourcing(authed_client)
    _low_stock_part(authed_client, mpn="SHARED-MPN")

    client_b = TestClient(app)
    signup_user(client_b)
    _configure_sourcing(client_b)
    _low_stock_part(client_b, mpn="SHARED-MPN")

    first = authed_client.get("/api/reports/low-stock?include_sourcing=true")
    second = client_b.get("/api/reports/low-stock?include_sourcing=true")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_offer = first.json()["data"]["rows"][0]["sourcing"]["best_offer"]
    second_offer = second.json()["data"]["rows"][0]["sourcing"]["best_offer"]
    assert first_offer["distributor"] != second_offer["distributor"]
    assert [call["mpn"] for call in _FakeTrustedPartsClient.calls] == [
        "SHARED-MPN",
        "SHARED-MPN",
    ]
