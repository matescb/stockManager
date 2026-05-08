from __future__ import annotations

import uuid
from datetime import datetime

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
from tests._factories import create_part, signup_user


def _configure_sourcing(client: TestClient, *, use_cached: bool = False) -> None:
    r = client.patch(
        "/api/workspaces/current",
        json={
            "sourcing_provider": "trustedparts",
            "sourcing_company_id": "company-123",
            "sourcing_api_key": "api-key-456",
            "sourcing_country_code": "CZ",
            "sourcing_currency_code": "EUR",
            "sourcing_preferred_distributors": ["DigiKey"],
            "sourcing_use_cached_for_dashboards": use_cached,
        },
    )
    assert r.status_code == 200, r.text


def _current_workspace_id(client: TestClient) -> str:
    r = client.get("/api/workspaces/current")
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


class _FakeTrustedPartsClient:
    calls: list[dict] = []

    def __init__(self) -> None:
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
                "queries": [item.model_dump(exclude_none=True) for item in queries],
                "country_code": self.country_code,
                "currency_code": self.currency_code,
                "in_stock_only": in_stock_only,
                "distributors": distributors,
                "use_cached_data": use_cached_data,
            }
        )
        return SourcingSearchRaw(
            offers=[
                SourcingOffer(
                    mpn=query.search_token,
                    manufacturer="ST",
                    description="Test offer",
                    distributors=[
                        SourcingDistributor(
                            name="DigiKey",
                            sku=f"{query.search_token}-DK",
                            stock=42,
                            unit_price=1.23,
                            currency=self.currency_code,
                            product_url="https://www.trustedparts.com/product",
                        )
                    ],
                    links=SourcingLinks(
                        primary=f"https://www.trustedparts.com/search/{query.search_token}"
                    ),
                )
            ],
            request_id=f"req-{len(self.calls)}",
        )


@pytest.fixture(autouse=True)
def reset_sourcing_state(monkeypatch):
    original_limiter_enabled = _ratelimit_mod.limiter.enabled
    _ratelimit_mod.limiter.enabled = False
    _FakeTrustedPartsClient.calls = []
    BUDGET._events.clear()
    try:
        _ratelimit_mod.limiter.reset()
    except Exception:
        pass
    monkeypatch.setattr(
        "app.domain.sourcing.service.make_sourcing_provider",
        lambda _workspace: _FakeTrustedPartsClient(),
    )
    yield
    _ratelimit_mod.limiter.enabled = original_limiter_enabled
    BUDGET._events.clear()
    try:
        _ratelimit_mod.limiter.reset()
    except Exception:
        pass


def test_part_with_mpn_returns_offers(authed_client, monkeypatch):
    _configure_sourcing(authed_client)
    part_id = create_part(authed_client, name="STM32", mpn="STM32F103C8T6")
    ttl_values: list[int] = []

    from app.domain.sourcing import cache as sourcing_cache

    original_get_or_fetch = sourcing_cache.get_or_fetch

    def spy_get_or_fetch(*args, **kwargs):
        ttl_values.append(kwargs["ttl_seconds"])
        return original_get_or_fetch(*args, **kwargs)

    monkeypatch.setattr(sourcing_cache, "get_or_fetch", spy_get_or_fetch)

    r = authed_client.get(
        f"/api/parts/{part_id}/sourcing",
        params={
            "country": "de",
            "currency": "eur",
            "in_stock_only": "true",
            "distributors": "DigiKey,Mouser",
        },
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"]["category"] == "ok"
    data = body["data"]
    assert data["mpn"] == "STM32F103C8T6"
    assert data["powered_by"] == "TrustedParts"
    datetime.fromisoformat(data["fetched_at"])
    assert data["cache_hit"] is False
    assert data["reason"] == "ok"
    assert data["links"]["primary"] == "https://www.trustedparts.com/"
    assert data["offers"][0]["distributors"][0]["name"] == "DigiKey"
    assert ttl_values == [1800]
    assert _FakeTrustedPartsClient.calls == [
        {
            "queries": [{"search_token": "STM32F103C8T6"}],
            "country_code": "DE",
            "currency_code": "EUR",
            "in_stock_only": True,
            "distributors": ["DigiKey", "Mouser"],
            "use_cached_data": False,
        }
    ]


def test_part_without_mpn_returns_no_mpn_reason_and_skips_network(authed_client):
    part_id = create_part(authed_client, name="Manual part")

    r = authed_client.get(f"/api/parts/{part_id}/sourcing")

    assert r.status_code == 200, r.text
    assert r.json()["data"] == {
        "offers": [],
        "reason": "no_mpn",
        "cache_hit": None,
    }
    assert _FakeTrustedPartsClient.calls == []


def test_foreign_part_returns_404_envelope(authed_client):
    foreign_client = TestClient(app)
    signup_user(foreign_client)
    foreign_part_id = create_part(foreign_client, name="Foreign", mpn="BAT54C")

    r = authed_client.get(f"/api/parts/{foreign_part_id}/sourcing")

    assert r.status_code == 404, r.text
    body = r.json()
    assert body["data"] is None
    assert body["status"]["category"] == "not_found"


def test_unconfigured_returns_409(authed_client, monkeypatch):
    monkeypatch.setattr(
        "app.domain.sourcing.service.make_sourcing_provider",
        lambda _workspace: None,
    )
    part_id = create_part(authed_client, name="BAT54", mpn="BAT54C")

    r = authed_client.get(f"/api/parts/{part_id}/sourcing")

    assert r.status_code == 409, r.text
    assert r.json()["status"] == {
        "category": "conflict",
        "message": "sourcing not configured",
    }


def test_budget_blocked_returns_503(authed_client):
    _configure_sourcing(authed_client)
    part_id = create_part(authed_client, name="BAT54", mpn="BAT54C")
    BUDGET.record(uuid.UUID(_current_workspace_id(authed_client)), 250)

    r = authed_client.get(f"/api/parts/{part_id}/sourcing")

    assert r.status_code == 503, r.text
    assert r.json()["status"] == {
        "category": "server_error",
        "message": "sourcing budget exhausted",
    }


def test_rate_limit_after_60_per_minute(authed_client):
    _ratelimit_mod.limiter.enabled = True
    _configure_sourcing(authed_client)
    part_id = create_part(authed_client, name="MAX232", mpn="MAX232")

    for _index in range(60):
        r = authed_client.get(f"/api/parts/{part_id}/sourcing")
        assert r.status_code == 200, r.text

    r = authed_client.get(f"/api/parts/{part_id}/sourcing")

    assert r.status_code == 429, r.text
    assert r.json()["status"]["category"] == "rate_limited"


def test_cache_hit_on_second_call(authed_client):
    _configure_sourcing(authed_client)
    part_id = create_part(authed_client, name="BAV99", mpn="BAV99")

    first = authed_client.get(f"/api/parts/{part_id}/sourcing")
    second = authed_client.get(f"/api/parts/{part_id}/sourcing")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["data"]["cache_hit"] is False
    assert second.json()["data"]["cache_hit"] is True
    assert len(_FakeTrustedPartsClient.calls) == 1


def test_workspace_isolation_two_parts_same_mpn(authed_client):
    _configure_sourcing(authed_client)
    part_a = create_part(authed_client, name="A BAV99", mpn="BAV99")

    client_b = TestClient(app)
    signup_user(client_b)
    _configure_sourcing(client_b)
    part_b = create_part(client_b, name="B BAV99", mpn="BAV99")

    first = authed_client.get(f"/api/parts/{part_a}/sourcing")
    second = client_b.get(f"/api/parts/{part_b}/sourcing")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["data"]["cache_hit"] is False
    assert second.json()["data"]["cache_hit"] is False
    assert len(_FakeTrustedPartsClient.calls) == 2
