from __future__ import annotations

import httpx
import pytest

import app.domain.sourcing.client as sourcing_client
from app.domain.sourcing.client import (
    TP_TIMEOUT_SECONDS,
    SourcingAuthError,
    SourcingRateLimitError,
    SourcingTimeoutError,
    SourcingUpstreamError,
    SourcingValidationError,
    TrustedPartsClient,
)
from app.domain.sourcing.schemas import SourcingQuery, SourcingSearchRaw


def _client() -> TrustedPartsClient:
    return TrustedPartsClient(
        company_id="company-1",
        api_key="api-key-1",
        country_code="CZ",
        currency_code="EUR",
        user_agent="stockManager/test workspace=ws-1",
    )


def _query(token: str = "STM32F103C8T6") -> SourcingQuery:
    return SourcingQuery(search_token=token)


def _trustedparts_response() -> dict:
    return {
        "RequestId": "req-1",
        "PartResults": [
            {
                "PartNumber": "STM32F103C8T6",
                "Manufacturer": "STMicroelectronics",
                "ProductUrl": "https://www.trustedparts.com/en/part/...",
                "Distributors": [
                    {
                        "Name": "DigiKey",
                        "DistributorResults": [
                            {
                                "Description": "MCU 32-bit ARM Cortex-M3",
                                "DistributorPartNumber": "497-1234-1-ND",
                                "Stock": {"QuantityOnHand": 1200},
                                "Links": [
                                    {
                                        "Type": "datasheet",
                                        "Url": "https://example.com/datasheet.pdf",
                                    },
                                    {
                                        "Type": "distributor",
                                        "Url": "https://example.com/product",
                                    },
                                ],
                                "Pricing": {
                                    "CurrencyCode": "EUR",
                                    "MinimumQuantity": 1,
                                    "Prices": [
                                        {"Quantity": 1, "Amount": 2.5},
                                        {"Quantity": 10, "Amount": 2.1},
                                    ],
                                },
                                "Packaging": [
                                    {
                                        "PackageType": "Cut Tape",
                                        "MinimumOrderQuantity": 1,
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }


def test_happy_path_returns_offers(monkeypatch):
    monkeypatch.setattr(
        sourcing_client,
        "_post_tp",
        lambda url, json: (200, _trustedparts_response()),
    )

    result = _client().search([_query()])

    assert isinstance(result, SourcingSearchRaw)
    assert result.request_id == "req-1"
    assert result.offers[0].mpn == "STM32F103C8T6"
    assert result.offers[0].manufacturer == "STMicroelectronics"
    assert result.offers[0].description == "MCU 32-bit ARM Cortex-M3"
    assert result.offers[0].links.datasheet == "https://example.com/datasheet.pdf"
    distributor = result.offers[0].distributors[0]
    assert distributor.name == "DigiKey"
    assert distributor.sku == "497-1234-1-ND"
    assert distributor.packaging == "Cut Tape"
    assert distributor.moq == 1
    assert distributor.stock == 1200
    assert distributor.unit_price == 2.5
    assert distributor.currency == "EUR"
    assert distributor.price_breaks[1].quantity == 10
    assert distributor.price_breaks[1].unit_price == 2.1
    assert distributor.product_url == "https://example.com/product"


def test_request_payload_shape(monkeypatch):
    captured: dict[str, dict] = {}

    def fake_post(url, json):
        captured["json"] = json
        return 200, {"PartResults": []}

    monkeypatch.setattr(sourcing_client, "_post_tp", fake_post)

    _client().search(
        [
            SourcingQuery(
                search_token="BAT54C",
                manufacturers=["onsemi"],
            ),
            _query("BAV99"),
        ],
        exact_match=False,
        distributors=["DigiKey"],
        in_stock_only=True,
    )

    payload = captured["json"]
    assert payload["CompanyId"] == "company-1"
    assert payload["ApiKey"] == "api-key-1"
    assert payload["CountryCode"] == "CZ"
    assert payload["CurrencyCode"] == "EUR"
    assert payload["UserAgent"] == "stockManager/test workspace=ws-1"
    assert payload["InStockOnly"] is True
    assert payload["ExactMatch"] is True
    assert payload["IsCrawler"] is False
    assert payload["UseCachedData"] is False
    assert payload["Distributors"] == ["DigiKey"]
    assert payload["Queries"] == [
        {"SearchToken": "BAT54C", "Manufacturers": ["onsemi"]},
        {"SearchToken": "BAV99"},
    ]
    assert "SourceIp" not in payload


def test_too_many_queries_raises_before_network(monkeypatch):
    calls = 0

    def fake_post(url, json):
        nonlocal calls
        calls += 1
        return 200, {"PartResults": []}

    monkeypatch.setattr(sourcing_client, "_post_tp", fake_post)

    with pytest.raises(ValueError):
        _client().search([_query(f"MPN-{index}") for index in range(51)])

    assert calls == 0


def test_empty_queries_raises():
    with pytest.raises(ValueError):
        _client().search([])


def test_401_maps_to_auth_error(monkeypatch):
    monkeypatch.setattr(sourcing_client, "_post_tp", lambda url, json: (401, {}))

    with pytest.raises(SourcingAuthError):
        _client().search([_query()])


def test_429_maps_to_rate_limit_error(monkeypatch):
    monkeypatch.setattr(sourcing_client, "_post_tp", lambda url, json: (429, {}))

    with pytest.raises(SourcingRateLimitError):
        _client().search([_query()])


def test_500_maps_to_upstream_error(monkeypatch):
    monkeypatch.setattr(sourcing_client, "_post_tp", lambda url, json: (500, {}))

    with pytest.raises(SourcingUpstreamError):
        _client().search([_query()])


def test_timeout_maps_to_timeout_error(monkeypatch):
    def timeout(url, json):
        raise httpx.TimeoutException("simulated timeout")

    monkeypatch.setattr(sourcing_client, "_post_tp", timeout)

    with pytest.raises(SourcingTimeoutError):
        _client().search([_query()])


def test_unparseable_body_maps_to_validation_error(monkeypatch):
    monkeypatch.setattr(
        sourcing_client,
        "_post_tp",
        lambda url, json: (200, {"PartResults": {"bad": "shape"}}),
    )

    with pytest.raises(SourcingValidationError):
        _client().search([_query()])


def test_dtos_round_trip_cleanly(monkeypatch):
    monkeypatch.setattr(
        sourcing_client,
        "_post_tp",
        lambda url, json: (200, _trustedparts_response()),
    )

    result = _client().search([_query()])
    parsed = SourcingSearchRaw.model_validate(result.model_dump())

    assert parsed.model_dump() == result.model_dump()


def test_default_timeout_is_eight_seconds():
    assert TP_TIMEOUT_SECONDS == 8.0
