from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import app.core.ratelimit as _ratelimit_mod
from app.domain.sourcing.budget import BUDGET
from app.domain.sourcing.schemas import (
    SourcingDistributor,
    SourcingLinks,
    SourcingOffer,
    SourcingPriceBreak,
    SourcingQuery,
    SourcingRohsCompliance,
    SourcingSearchRaw,
    SourcingSpecification,
)
from app.main import app
from tests._factories import create_part, signup_user


def _configure_sourcing(
    client: TestClient,
    *,
    use_cached: bool = False,
    currency_code: str | None = "EUR",
) -> None:
    r = client.patch(
        "/api/workspaces/current",
        json={
            "sourcing_provider": "trustedparts",
            "sourcing_company_id": "company-123",
            "sourcing_api_key": "api-key-456",
            "sourcing_country_code": "CZ",
            "sourcing_currency_code": currency_code,
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
    returned_currency: str | None = None
    unit_price: float = 1.23
    price_breaks: list[SourcingPriceBreak] = []
    lifecycle_risk: str | None = None
    supply_chain_risk: str | None = None
    is_affected_by_tariff: bool | None = None
    manufacturer_id: int | None = None
    specifications: list[SourcingSpecification] = []
    distributor_id: int | None = None
    rohs_compliance: list[SourcingRohsCompliance] = []
    availability_text: str | None = None
    quantity_multiple: int | None = None
    tp_current_date: datetime | None = None
    tp_response_time: str | None = None

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
        returned_currency = self.returned_currency or self.currency_code
        return SourcingSearchRaw(
            offers=[
                SourcingOffer(
                    mpn=query.search_token,
                    manufacturer="ST",
                    description="Test offer",
                    lifecycle_risk=self.lifecycle_risk,
                    supply_chain_risk=self.supply_chain_risk,
                    is_affected_by_tariff=self.is_affected_by_tariff,
                    manufacturer_id=self.manufacturer_id,
                    specifications=self.specifications,
                    distributors=[
                        SourcingDistributor(
                            distributor_id=self.distributor_id,
                            name="DigiKey",
                            sku=f"{query.search_token}-DK",
                            stock=42,
                            unit_price=self.unit_price,
                            currency=returned_currency,
                            price_breaks=self.price_breaks,
                            product_url="https://www.trustedparts.com/product",
                            rohs_compliance=self.rohs_compliance,
                            availability_text=self.availability_text,
                            quantity_multiple=self.quantity_multiple,
                        )
                    ],
                    links=SourcingLinks(
                        primary=f"https://www.trustedparts.com/search/{query.search_token}"
                    ),
                )
            ],
            request_id=f"req-{len(self.calls)}",
            tp_current_date=self.tp_current_date,
            tp_response_time=self.tp_response_time,
        )


@pytest.fixture(autouse=True)
def reset_sourcing_state(monkeypatch):
    original_limiter_enabled = _ratelimit_mod.limiter.enabled
    _ratelimit_mod.limiter.enabled = False
    _FakeTrustedPartsClient.calls = []
    _FakeTrustedPartsClient.returned_currency = None
    _FakeTrustedPartsClient.unit_price = 1.23
    _FakeTrustedPartsClient.price_breaks = []
    _FakeTrustedPartsClient.lifecycle_risk = None
    _FakeTrustedPartsClient.supply_chain_risk = None
    _FakeTrustedPartsClient.is_affected_by_tariff = None
    _FakeTrustedPartsClient.manufacturer_id = None
    _FakeTrustedPartsClient.specifications = []
    _FakeTrustedPartsClient.distributor_id = None
    _FakeTrustedPartsClient.rohs_compliance = []
    _FakeTrustedPartsClient.availability_text = None
    _FakeTrustedPartsClient.quantity_multiple = None
    _FakeTrustedPartsClient.tp_current_date = None
    _FakeTrustedPartsClient.tp_response_time = None
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


def test_per_part_response_includes_all_8_new_fields(authed_client):
    _configure_sourcing(authed_client)
    _FakeTrustedPartsClient.lifecycle_risk = "Low"
    _FakeTrustedPartsClient.supply_chain_risk = "Elevated"
    _FakeTrustedPartsClient.is_affected_by_tariff = True
    _FakeTrustedPartsClient.manufacturer_id = 12345
    _FakeTrustedPartsClient.specifications = [
        SourcingSpecification(key="Package", value="LQFP-48")
    ]
    _FakeTrustedPartsClient.distributor_id = 9876
    _FakeTrustedPartsClient.rohs_compliance = [
        SourcingRohsCompliance(
            region="EU",
            is_compliant=True,
            description="RoHS compliant",
        )
    ]
    _FakeTrustedPartsClient.availability_text = "In Stock"
    _FakeTrustedPartsClient.quantity_multiple = 5
    _FakeTrustedPartsClient.price_breaks = [
        SourcingPriceBreak(
            quantity=1,
            unit_price=1.23,
            formatted_amount="$1.23",
            text="1+ $1.23",
        )
    ]
    _FakeTrustedPartsClient.tp_current_date = datetime(
        2026,
        5,
        10,
        12,
        tzinfo=timezone.utc,
    )
    _FakeTrustedPartsClient.tp_response_time = "00:00:01.234"
    part_id = create_part(authed_client, name="STM32", mpn="STM32F103C8T6")

    r = authed_client.get(f"/api/parts/{part_id}/sourcing")

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert datetime.fromisoformat(data["tp_current_date"]) == datetime(
        2026,
        5,
        10,
        12,
        tzinfo=timezone.utc,
    )
    assert data["tp_response_time"] == "00:00:01.234"
    offer = data["offers"][0]
    assert offer["lifecycle_risk"] == "Low"
    assert offer["supply_chain_risk"] == "Elevated"
    assert offer["is_affected_by_tariff"] is True
    assert offer["manufacturer_id"] == 12345
    assert offer["specifications"] == [{"key": "Package", "value": "LQFP-48"}]
    distributor = offer["distributors"][0]
    assert distributor["distributor_id"] == 9876
    assert distributor["rohs_compliance"] == [
        {
            "region": "EU",
            "is_compliant": True,
            "description": "RoHS compliant",
        }
    ]
    assert distributor["availability_text"] == "In Stock"
    assert distributor["quantity_multiple"] == 5
    assert distributor["price_breaks"][0]["formatted_amount"] == "$1.23"
    assert distributor["price_breaks"][0]["text"] == "1+ $1.23"


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
    assert body["code"] == "resource.not_found"
    assert body["status"]["category"] == "not_found"


def test_unconfigured_returns_409(authed_client, monkeypatch):
    monkeypatch.setattr(
        "app.domain.sourcing.service.make_sourcing_provider",
        lambda _workspace: None,
    )
    part_id = create_part(authed_client, name="BAT54", mpn="BAT54C")

    r = authed_client.get(f"/api/parts/{part_id}/sourcing")

    assert r.status_code == 409, r.text
    assert r.json()["code"] == "sourcing.workspace_not_configured"
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
    assert r.json()["code"] == "sourcing.budget_exhausted"
    assert r.json()["status"] == {
        "category": "server_error",
        "message": "sourcing budget exhausted",
    }


def test_distributors_query_param_capped_at_25(authed_client):
    _configure_sourcing(authed_client)
    part_id = create_part(authed_client, name="BAT54", mpn="BAT54C")

    r = authed_client.get(
        f"/api/parts/{part_id}/sourcing",
        params={"distributors": ",".join(f"D{index}" for index in range(26))},
    )

    assert r.status_code == 422, r.text
    assert r.json()["code"] == "sourcing.too_many_distributors"
    assert r.json()["status"] == {
        "category": "validation_error",
        "message": "distributors list capped at 25",
    }
    assert _FakeTrustedPartsClient.calls == []


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


def test_sourcing_response_converts_when_distributor_returns_different_currency(
    authed_client,
    monkeypatch,
):
    _configure_sourcing(authed_client)
    _FakeTrustedPartsClient.returned_currency = "USD"
    _FakeTrustedPartsClient.unit_price = 2.5
    _FakeTrustedPartsClient.price_breaks = [
        SourcingPriceBreak(quantity=1, unit_price=2.5),
        SourcingPriceBreak(quantity=10, unit_price=2.0),
    ]
    monkeypatch.setattr(
        "app.domain.fx.rates.get_or_fetch_today",
        lambda _db, *, on_date: {
            "EUR": Decimal("1"),
            "USD": Decimal("2"),
        },
    )
    part_id = create_part(authed_client, name="FX part", mpn="FX-USD")

    r = authed_client.get(f"/api/parts/{part_id}/sourcing", params={"currency": "eur"})

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    distributor = data["offers"][0]["distributors"][0]
    assert data["fx_status"] is None
    assert distributor["unit_price"] == 2.5
    assert distributor["currency"] == "USD"
    assert Decimal(distributor["unit_price_converted"]) == Decimal("1.2500")
    assert distributor["currency_displayed"] == "EUR"
    assert distributor["fx_converted"] is True
    assert distributor["fx_rate_date"] == date.today().isoformat()
    assert Decimal(distributor["price_breaks_converted"][1]["unit_price"]) == Decimal("1.0000")


def test_sourcing_response_skips_conversion_when_workspace_currency_null(authed_client):
    _configure_sourcing(authed_client, currency_code=None)
    _FakeTrustedPartsClient.returned_currency = "USD"
    part_id = create_part(authed_client, name="Native currency", mpn="FX-NATIVE")

    r = authed_client.get(f"/api/parts/{part_id}/sourcing")

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    distributor = data["offers"][0]["distributors"][0]
    assert data["fx_status"] is None
    assert distributor["currency"] == "USD"
    assert distributor["unit_price_converted"] is None
    assert distributor["currency_displayed"] == "USD"
    assert distributor["fx_converted"] is None


def test_sourcing_response_surfaces_fx_status_unavailable(authed_client, monkeypatch):
    _configure_sourcing(authed_client)
    _FakeTrustedPartsClient.returned_currency = "USD"
    monkeypatch.setattr(
        "app.domain.fx.rates.get_or_fetch_today",
        lambda _db, *, on_date: {"EUR": Decimal("1")},
    )
    part_id = create_part(authed_client, name="Unknown FX", mpn="FX-MISS")

    r = authed_client.get(f"/api/parts/{part_id}/sourcing", params={"currency": "eur"})

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    distributor = data["offers"][0]["distributors"][0]
    assert data["fx_status"] == "unavailable"
    assert distributor["unit_price"] == 1.23
    assert distributor["currency"] == "USD"
    assert distributor["unit_price_converted"] is None
    assert distributor["fx_converted"] is None
