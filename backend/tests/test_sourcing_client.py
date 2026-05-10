from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

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
        "CurrentDate": "2026-05-10T12:00:00Z",
        "ResponseTime": "00:00:01.234",
        "PartResults": [
            {
                "PartNumber": "STM32F103C8T6",
                "Manufacturer": "STMicroelectronics",
                "LifecycleRisk": "Low",
                "SupplyChainRisk": "Elevated",
                "IsAffectedByTariff": True,
                "ManufacturerId": 12345,
                "Specifications": [
                    {"Key": "Package", "Value": "LQFP-48"},
                    {"Key": "Core", "Value": "ARM Cortex-M3"},
                ],
                "ProductUrl": "https://www.trustedparts.com/en/part/...",
                "Distributors": [
                    {
                        "Id": 9876,
                        "Name": "DigiKey",
                        "DistributorResults": [
                            {
                                "Description": "MCU 32-bit ARM Cortex-M3",
                                "DistributorPartNumber": "497-1234-1-ND",
                                "Stock": {
                                    "Availability": "In Stock",
                                    "QuantityOnHand": 1200,
                                },
                                "Compliance": {
                                    "RoHS": [
                                        {
                                            "Region": "EU",
                                            "IsCompliant": True,
                                            "Description": "RoHS compliant",
                                        },
                                        {
                                            "Region": "CN",
                                            "IsCompliant": False,
                                            "Description": None,
                                        },
                                    ]
                                },
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
                                    "QuantityMultiple": 5.0,
                                    "Prices": [
                                        {
                                            "Quantity": 1,
                                            "Amount": 2.5,
                                            "FormattedAmount": "€2.50",
                                            "Text": "1+ €2.50",
                                        },
                                        {
                                            "Quantity": 10,
                                            "Amount": 2.1,
                                            "FormattedAmount": "€2.10",
                                            "Text": "10+ €2.10",
                                        },
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


def _search_response(monkeypatch, response: dict):
    monkeypatch.setattr(
        sourcing_client,
        "_post_tp",
        lambda url, json_body, headers: (200, response),
    )
    return _client().search([_query()])


def test_happy_path_returns_offers(monkeypatch):
    result = _search_response(monkeypatch, _trustedparts_response())

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


def test_lifecycle_risk_parsed_when_present(monkeypatch):
    result = _search_response(monkeypatch, _trustedparts_response())

    assert result.offers[0].lifecycle_risk == "Low"


def test_lifecycle_risk_is_none_when_absent(monkeypatch):
    response = deepcopy(_trustedparts_response())
    response["PartResults"][0].pop("LifecycleRisk")

    result = _search_response(monkeypatch, response)

    assert result.offers[0].lifecycle_risk is None


def test_supply_chain_risk_parsed_when_present(monkeypatch):
    result = _search_response(monkeypatch, _trustedparts_response())

    assert result.offers[0].supply_chain_risk == "Elevated"


def test_supply_chain_risk_is_none_when_absent(monkeypatch):
    response = deepcopy(_trustedparts_response())
    response["PartResults"][0].pop("SupplyChainRisk")

    result = _search_response(monkeypatch, response)

    assert result.offers[0].supply_chain_risk is None


def test_is_affected_by_tariff_parsed_when_present(monkeypatch):
    result = _search_response(monkeypatch, _trustedparts_response())

    assert result.offers[0].is_affected_by_tariff is True


def test_manufacturer_id_parsed_when_present(monkeypatch):
    result = _search_response(monkeypatch, _trustedparts_response())

    assert result.offers[0].manufacturer_id == 12345


def test_specifications_parsed_as_list_of_key_value(monkeypatch):
    result = _search_response(monkeypatch, _trustedparts_response())

    assert [item.model_dump() for item in result.offers[0].specifications] == [
        {"key": "Package", "value": "LQFP-48"},
        {"key": "Core", "value": "ARM Cortex-M3"},
    ]


def test_specifications_empty_list_when_absent(monkeypatch):
    response = deepcopy(_trustedparts_response())
    response["PartResults"][0].pop("Specifications")

    result = _search_response(monkeypatch, response)

    assert result.offers[0].specifications == []


def test_tou_gated_null_fields_surface_none_and_empty_lists(monkeypatch):
    response = deepcopy(_trustedparts_response())
    part = response["PartResults"][0]
    part["LifecycleRisk"] = None
    part["SupplyChainRisk"] = None
    part["Specifications"] = None

    result = _search_response(monkeypatch, response)
    offer = result.offers[0]

    assert offer.lifecycle_risk is None
    assert offer.supply_chain_risk is None
    assert offer.specifications == []


def test_rohs_compliance_parsed_per_distributor_per_region(monkeypatch):
    result = _search_response(monkeypatch, _trustedparts_response())
    rohs = result.offers[0].distributors[0].rohs_compliance

    assert [item.model_dump() for item in rohs] == [
        {
            "region": "EU",
            "is_compliant": True,
            "description": "RoHS compliant",
        },
        {"region": "CN", "is_compliant": False, "description": None},
    ]


def test_rohs_compliance_empty_list_when_absent(monkeypatch):
    response = deepcopy(_trustedparts_response())
    response["PartResults"][0]["Distributors"][0]["DistributorResults"][0].pop(
        "Compliance"
    )

    result = _search_response(monkeypatch, response)

    assert result.offers[0].distributors[0].rohs_compliance == []


def test_availability_text_parsed_when_present(monkeypatch):
    result = _search_response(monkeypatch, _trustedparts_response())

    assert result.offers[0].distributors[0].availability_text == "In Stock"


def test_lead_time_days_remains_none_when_tp_omits_field(monkeypatch):
    response = deepcopy(_trustedparts_response())
    stock = response["PartResults"][0]["Distributors"][0]["DistributorResults"][0][
        "Stock"
    ]
    stock["Availability"] = "Ships in 12 weeks"

    result = _search_response(monkeypatch, response)
    distributor = result.offers[0].distributors[0]

    assert distributor.availability_text == "Ships in 12 weeks"
    assert distributor.lead_time_days is None


def test_quantity_multiple_rounded_to_int(monkeypatch):
    result = _search_response(monkeypatch, _trustedparts_response())

    assert result.offers[0].distributors[0].quantity_multiple == 5


def test_quantity_multiple_none_when_absent(monkeypatch):
    response = deepcopy(_trustedparts_response())
    response["PartResults"][0]["Distributors"][0]["DistributorResults"][0]["Pricing"].pop(
        "QuantityMultiple"
    )

    result = _search_response(monkeypatch, response)

    assert result.offers[0].distributors[0].quantity_multiple is None


def test_distributor_id_parsed(monkeypatch):
    result = _search_response(monkeypatch, _trustedparts_response())

    assert result.offers[0].distributors[0].distributor_id == 9876


def test_distributor_id_none_when_absent(monkeypatch):
    response = deepcopy(_trustedparts_response())
    response["PartResults"][0]["Distributors"][0].pop("Id")

    result = _search_response(monkeypatch, response)

    assert result.offers[0].distributors[0].distributor_id is None


def test_price_break_includes_formatted_amount_and_text(monkeypatch):
    response = deepcopy(_trustedparts_response())
    price = response["PartResults"][0]["Distributors"][0]["DistributorResults"][0]["Pricing"][
        "Prices"
    ][0]
    price["FormattedAmount"] = "$0.12"
    price["Text"] = "1+ $0.12"

    result = _search_response(monkeypatch, response)
    price_break = result.offers[0].distributors[0].price_breaks[0]

    assert price_break.formatted_amount == "$0.12"
    assert price_break.text == "1+ $0.12"


def test_response_envelope_includes_tp_current_date(monkeypatch):
    result = _search_response(monkeypatch, _trustedparts_response())

    assert result.tp_current_date == datetime(2026, 5, 10, 12, tzinfo=timezone.utc)


def test_response_envelope_includes_tp_response_time(monkeypatch):
    result = _search_response(monkeypatch, _trustedparts_response())

    assert result.tp_response_time == "00:00:01.234"


def test_request_payload_shape(monkeypatch):
    captured: dict[str, dict] = {}

    def fake_post(url, json_body, headers):
        captured["json"] = json_body
        captured["headers"] = headers
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
    assert "ApiKey" not in payload
    assert "CompanyId" not in payload
    assert captured["headers"]["X-Api-Key"] == "api-key-1"


def test_api_key_sent_in_x_api_key_header_not_body(monkeypatch):
    captured: dict[str, dict] = {}

    def fake_post(url, json_body, headers):
        captured["body"] = json_body
        captured["headers"] = headers
        return 200, {"PartResults": []}

    monkeypatch.setattr(sourcing_client, "_post_tp", fake_post)

    _client().search([_query()])

    assert captured["headers"]["X-Api-Key"] == "api-key-1"
    assert captured["body"].get("ApiKey") is None


def test_company_id_not_sent_in_request_body(monkeypatch):
    captured: dict[str, dict] = {}

    def fake_post(url, json_body, headers):
        captured["body"] = json_body
        return 200, {"PartResults": []}

    monkeypatch.setattr(sourcing_client, "_post_tp", fake_post)

    _client().search([_query()])

    assert captured["body"].get("CompanyId") is None


def test_search_request_includes_language_code_when_workspace_has_it_set(monkeypatch):
    captured: dict[str, dict] = {}
    client = TrustedPartsClient(
        company_id="company-1",
        api_key="api-key-1",
        country_code="CZ",
        currency_code="EUR",
        user_agent="stockManager/test workspace=ws-1",
        language_code="de",
    )

    def fake_post(url, json_body, headers):
        captured["body"] = json_body
        return 200, {"PartResults": []}

    monkeypatch.setattr(sourcing_client, "_post_tp", fake_post)

    client.search([_query()])

    assert captured["body"]["LanguageCode"] == "de"


def test_search_request_omits_language_code_when_workspace_has_none(monkeypatch):
    captured: dict[str, dict] = {}

    def fake_post(url, json_body, headers):
        captured["body"] = json_body
        return 200, {"PartResults": []}

    monkeypatch.setattr(sourcing_client, "_post_tp", fake_post)

    _client().search([_query()])

    assert "LanguageCode" not in captured["body"]


def test_api_key_never_logged(monkeypatch, caplog):
    api_key = "super-secret-api-key"
    client = TrustedPartsClient(
        company_id="company-1",
        api_key=api_key,
        country_code="CZ",
        currency_code="EUR",
        user_agent="stockManager/test workspace=ws-1",
    )

    monkeypatch.setattr(
        sourcing_client,
        "_post_tp",
        lambda url, json_body, headers: (
            200,
            {"Messages": ["using cached data"], "PartResults": []},
        ),
    )

    with caplog.at_level("INFO", logger=sourcing_client.__name__):
        client.search([_query()])

    assert api_key not in caplog.text


def test_too_many_queries_raises_before_network(monkeypatch):
    calls = 0

    def fake_post(url, json_body, headers):
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


def test_search_token_too_short_raises_value_error(monkeypatch):
    calls = 0

    def fake_post(url, json_body, headers):
        nonlocal calls
        calls += 1
        return 200, {"PartResults": []}

    monkeypatch.setattr(sourcing_client, "_post_tp", fake_post)

    with pytest.raises(ValueError, match="SearchToken"):
        _client().search([_query("x")])

    assert calls == 0


def test_search_token_too_long_raises_value_error(monkeypatch):
    calls = 0

    def fake_post(url, json_body, headers):
        nonlocal calls
        calls += 1
        return 200, {"PartResults": []}

    monkeypatch.setattr(sourcing_client, "_post_tp", fake_post)

    with pytest.raises(ValueError, match="SearchToken"):
        _client().search([_query("x" * 101)])

    assert calls == 0


def test_401_maps_to_auth_error(monkeypatch):
    monkeypatch.setattr(sourcing_client, "_post_tp", lambda url, json_body, headers: (401, {}))

    with pytest.raises(SourcingAuthError):
        _client().search([_query()])


def test_429_maps_to_rate_limit_error(monkeypatch):
    monkeypatch.setattr(sourcing_client, "_post_tp", lambda url, json_body, headers: (429, {}))

    with pytest.raises(SourcingRateLimitError):
        _client().search([_query()])


def test_500_maps_to_upstream_error(monkeypatch):
    monkeypatch.setattr(sourcing_client, "_post_tp", lambda url, json_body, headers: (500, {}))

    with pytest.raises(SourcingUpstreamError):
        _client().search([_query()])


def test_timeout_maps_to_timeout_error(monkeypatch):
    def timeout(url, json_body, headers):
        raise httpx.TimeoutException("simulated timeout")

    monkeypatch.setattr(sourcing_client, "_post_tp", timeout)

    with pytest.raises(SourcingTimeoutError):
        _client().search([_query()])


def test_unparseable_body_maps_to_validation_error(monkeypatch):
    monkeypatch.setattr(
        sourcing_client,
        "_post_tp",
        lambda url, json_body, headers: (200, {"PartResults": {"bad": "shape"}}),
    )

    with pytest.raises(SourcingValidationError):
        _client().search([_query()])


def test_malformed_response_raises_sourcing_validation_error(monkeypatch):
    monkeypatch.setattr(
        sourcing_client,
        "_post_tp",
        lambda url, json_body, headers: (200, {"bogus": True}),
    )

    with pytest.raises(SourcingValidationError):
        _client().search([_query()])


def test_validation_error_does_not_leak_response_body_to_logs(monkeypatch, caplog):
    secret_body_value = "body-secret-should-not-log"
    monkeypatch.setattr(
        sourcing_client,
        "_post_tp",
        lambda url, json_body, headers: (200, {"bogus": secret_body_value}),
    )

    with caplog.at_level("WARNING", logger=sourcing_client.__name__):
        with pytest.raises(SourcingValidationError):
            _client().search([_query()])

    assert secret_body_value not in caplog.text
    assert "request_hash=" in caplog.text
    assert "extra_forbidden" in caplog.text


def test_error_message_on_200_raises_sourcing_upstream_error(monkeypatch):
    monkeypatch.setattr(
        sourcing_client,
        "_post_tp",
        lambda url, json_body, headers: (
            200,
            {"ErrorMessage": "rate limit exceeded"},
        ),
    )

    with pytest.raises(SourcingUpstreamError, match="rate limit exceeded"):
        _client().search([_query()])


def test_messages_on_200_logged_not_raised(monkeypatch, caplog):
    monkeypatch.setattr(
        sourcing_client,
        "_post_tp",
        lambda url, json_body, headers: (
            200,
            {"Messages": ["using cached data"], "PartResults": []},
        ),
    )

    with caplog.at_level("INFO", logger=sourcing_client.__name__):
        result = _client().search([_query()])

    assert result.offers == []
    records = [
        record
        for record in caplog.records
        if getattr(record, "tp_message", None) == "using cached data"
    ]
    assert records
    assert "tp_message" in records[0].getMessage()
    assert "using cached data" in records[0].getMessage()


def test_dtos_round_trip_cleanly(monkeypatch):
    monkeypatch.setattr(
        sourcing_client,
        "_post_tp",
        lambda url, json_body, headers: (200, _trustedparts_response()),
    )

    result = _client().search([_query()])
    parsed = SourcingSearchRaw.model_validate(result.model_dump())

    assert parsed.model_dump() == result.model_dump()


def test_default_timeout_is_eight_seconds():
    assert TP_TIMEOUT_SECONDS == 8.0
