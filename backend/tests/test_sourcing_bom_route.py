from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

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
from tests._factories import add_stock, create_part, create_project_with_bom, signup_user


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


def _current_workspace_id(client: TestClient) -> str:
    r = client.get("/api/workspaces/current")
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _offer(
    mpn: str,
    *,
    distributor: str = "DigiKey",
    stock: int = 100,
    unit_price: float = 1.0,
    moq: int | None = 1,
    lead_time_days: int | None = 3,
    currency: str = "EUR",
    lifecycle_risk: str | None = None,
    supply_chain_risk: str | None = None,
    is_affected_by_tariff: bool | None = None,
    rohs_compliance: list[SourcingRohsCompliance] | None = None,
    availability_text: str | None = None,
    quantity_multiple: int | None = None,
) -> SourcingOffer:
    return SourcingOffer(
        mpn=mpn,
        manufacturer="TestCo",
        description=f"Offer for {mpn}",
        lifecycle_risk=lifecycle_risk,
        supply_chain_risk=supply_chain_risk,
        is_affected_by_tariff=is_affected_by_tariff,
        manufacturer_id=12345,
        specifications=[SourcingSpecification(key="Package", value="SOT-23")],
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
                rohs_compliance=rohs_compliance or [],
                availability_text=availability_text,
                quantity_multiple=quantity_multiple,
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
                "country_code": self.country_code,
                "currency_code": self.currency_code,
                "in_stock_only": in_stock_only,
                "distributors": distributors,
                "use_cached_data": use_cached_data,
            }
        )
        offers = self.offers_by_mpn.get(query.search_token)
        if offers is None and self.workspace_id is not None:
            distributor = f"WS-{str(self.workspace_id)[:8]}"
            offers = [_offer(query.search_token, distributor=distributor)]
        if offers is None:
            offers = [_offer(query.search_token)]
        return SourcingSearchRaw(
            offers=offers,
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


def _single_line_project(client: TestClient, *, mpn: str = "BOM-MPN", quantity: int = 10) -> str:
    part_id = create_part(client, name=mpn, mpn=mpn)
    return create_project_with_bom(
        client,
        f"Project {mpn}",
        [{"part_id": part_id, "quantity": quantity}],
    )


def _post_sourcing(
    client: TestClient,
    project_id: str,
    build_quantity: int = 1,
    *,
    currency: str | None = None,
):
    body: dict[str, Any] = {"build_quantity": build_quantity}
    if currency is not None:
        body["currency"] = currency
    return client.post(
        f"/api/projects/{project_id}/sourcing",
        json=body,
    )


def test_basic_bom_returns_enriched_lines(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="STM32F103C8T6", quantity=10)
    _FakeTrustedPartsClient.offers_by_mpn = {
        "STM32F103C8T6": [
            _offer("STM32F103C8T6", distributor="DigiKey", stock=20, unit_price=0.2),
            _offer("STM32F103C8T6", distributor="Mouser", stock=40, unit_price=0.1),
        ]
    }

    r = _post_sourcing(authed_client, project_id, build_quantity=2)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"]["category"] == "ok"
    data = body["data"]
    datetime.fromisoformat(data["fetched_at"])
    assert data["powered_by"] == "TrustedParts"
    assert data["partial"] is False
    assert data["coverage"]["total_lines"] == 1
    assert data["coverage"]["best_single_distributor"] == "DigiKey"
    assert data["capacity"]["can_build_now"] == 0
    assert data["capacity"]["can_build_after_purchase"] == 6
    assert Decimal(data["capacity"]["total_bom_cost"]) == Decimal("2.00")
    assert Decimal(data["capacity"]["purchase_to_pay_cost"]) == Decimal("2.00")
    assert data["capacity"]["est_purchase_cost"] == data["capacity"]["purchase_to_pay_cost"]
    row = data["rows"][0]
    assert row["required"] == 20
    assert row["available"] == 0
    assert row["short_by"] == 20
    assert row["authorized_stock"] == 60
    assert row["best_offer"]["distributor"] == "Mouser"
    assert isinstance(row["best_offer"]["unit_price"], str)
    assert isinstance(row["est_extended_cost"], str)
    assert row["reason"] == "ok"
    assert row["cache_hit"] is False
    assert row["fx_status"] is None


def test_bom_response_includes_lifecycle_risk_per_offer(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="RISK-MPN")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "RISK-MPN": [_offer("RISK-MPN", lifecycle_risk="Medium")]
    }

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    row = r.json()["data"]["rows"][0]
    assert row["offers"][0]["lifecycle_risk"] == "Medium"
    assert row["best_offer"]["lifecycle_risk"] == "Medium"


def test_bom_response_includes_is_affected_by_tariff_per_offer(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="TARIFF-MPN")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "TARIFF-MPN": [_offer("TARIFF-MPN", is_affected_by_tariff=True)]
    }

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    row = r.json()["data"]["rows"][0]
    assert row["offers"][0]["is_affected_by_tariff"] is True
    assert row["best_offer"]["is_affected_by_tariff"] is True


def test_bom_response_includes_distributor_availability_and_quantity_multiple(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="AVAIL-MPN")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "AVAIL-MPN": [
            _offer(
                "AVAIL-MPN",
                availability_text="Ships in 12 weeks",
                quantity_multiple=5,
            )
        ]
    }

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    row = r.json()["data"]["rows"][0]
    assert row["offers"][0]["availability_text"] == "Ships in 12 weeks"
    assert row["offers"][0]["quantity_multiple"] == 5
    assert row["best_offer"]["availability_text"] == "Ships in 12 weeks"
    assert row["best_offer"]["quantity_multiple"] == 5


def test_bom_response_offers_in_workspace_currency_when_currency_passed(
    authed_client,
    monkeypatch,
):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="FX-MPN")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "FX-MPN": [
            _offer("FX-MPN", distributor="DigiKey", unit_price=2.0, currency="USD"),
            _offer("FX-MPN", distributor="Mouser", unit_price=1.5, currency="GBP"),
        ]
    }
    calls = 0

    def fake_rates(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {
            "EUR": Decimal("1"),
            "USD": Decimal("2"),
            "GBP": Decimal("0.5"),
        }

    monkeypatch.setattr("app.domain.sourcing.service.fx_rates.get_or_fetch_today", fake_rates)

    r = _post_sourcing(authed_client, project_id, currency="EUR")

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["fx_status"] == "ok"
    row = data["rows"][0]
    assert row["fx_status"] is None
    offers = {offer["distributor"]: offer for offer in row["offers"]}
    assert Decimal(offers["DigiKey"]["unit_price"]) == Decimal("2.0")
    assert Decimal(offers["DigiKey"]["unit_price_converted"]) == Decimal("1.0000")
    assert offers["DigiKey"]["currency"] == "USD"
    assert offers["DigiKey"]["currency_displayed"] == "EUR"
    assert offers["DigiKey"]["fx_converted"] is True
    assert Decimal(offers["DigiKey"]["price_breaks_converted"][0]["unit_price"]) == Decimal(
        "1.0000"
    )
    assert Decimal(offers["Mouser"]["unit_price_converted"]) == Decimal("3.0000")
    assert row["best_offer"]["currency_displayed"] == "EUR"
    assert calls == 1


def test_bom_response_preserves_native_currency_when_fx_unavailable_and_surfaces_partial(
    authed_client,
    monkeypatch,
):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="FX-PARTIAL")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "FX-PARTIAL": [
            _offer("FX-PARTIAL", distributor="DigiKey", unit_price=2.0, currency="USD"),
            _offer("FX-PARTIAL", distributor="Local", unit_price=9.0, currency="AUD"),
        ]
    }
    monkeypatch.setattr(
        "app.domain.sourcing.service.fx_rates.get_or_fetch_today",
        lambda *_args, **_kwargs: {"EUR": Decimal("1"), "USD": Decimal("2")},
    )

    r = _post_sourcing(authed_client, project_id, currency="EUR")

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["fx_status"] == "partial"
    row = data["rows"][0]
    assert row["fx_status"] == "unavailable"
    offers = {offer["distributor"]: offer for offer in row["offers"]}
    assert offers["DigiKey"]["currency_displayed"] == "EUR"
    assert Decimal(offers["DigiKey"]["unit_price_converted"]) == Decimal("1.0000")
    assert offers["Local"]["currency"] == "AUD"
    assert offers["Local"]["currency_displayed"] == "AUD"
    assert offers["Local"]["unit_price_converted"] is None
    assert offers["Local"]["fx_converted"] is None


def test_bom_response_no_conversion_when_currency_omitted(authed_client, monkeypatch):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="FX-NATIVE")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "FX-NATIVE": [_offer("FX-NATIVE", unit_price=2.0, currency="USD")]
    }

    def fail_fetch(*_args, **_kwargs):
        raise AssertionError("FX rates should not be fetched without a requested currency")

    monkeypatch.setattr("app.domain.sourcing.service.fx_rates.get_or_fetch_today", fail_fetch)

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["fx_status"] is None
    offer = data["rows"][0]["offers"][0]
    assert offer["currency"] == "USD"
    assert offer["currency_displayed"] == "USD"
    assert offer["unit_price_converted"] is None
    assert offer["price_breaks_converted"] is None


def test_bom_row_reports_no_mpn_without_provider_call(authed_client):
    _configure_sourcing(authed_client)
    part_id = create_part(authed_client, name="Unnumbered", mpn=None)
    project_id = create_project_with_bom(
        authed_client,
        "Missing MPN BOM",
        [{"part_id": part_id, "quantity": 3}],
    )

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    row = r.json()["data"]["rows"][0]
    assert row["mpn"] is None
    assert row["offers"] == []
    assert row["reason"] == "no_mpn"
    assert row["cache_hit"] is None
    assert _FakeTrustedPartsClient.calls == []


def test_bom_row_reports_no_offers_and_cache_hit(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="NO-OFFERS")
    _FakeTrustedPartsClient.offers_by_mpn = {"NO-OFFERS": []}

    first = _post_sourcing(authed_client, project_id)
    second = _post_sourcing(authed_client, project_id)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_row = first.json()["data"]["rows"][0]
    second_row = second.json()["data"]["rows"][0]
    assert first_row["reason"] == "no_offers"
    assert first_row["cache_hit"] is False
    assert second_row["reason"] == "no_offers"
    assert second_row["cache_hit"] is True
    assert len(_FakeTrustedPartsClient.calls) == 1


def test_bom_with_substitutes_dedupes_mpns(authed_client, monkeypatch):
    _configure_sourcing(authed_client)
    main = create_part(authed_client, name="Main", mpn="MAIN-MPN")
    alt = create_part(authed_client, name="Alt", mpn="ALT-MPN")
    r = authed_client.post(f"/api/parts/{main}/substitutes", json={"substitute_part_id": alt})
    assert r.status_code == 200, r.text
    project_id = create_project_with_bom(
        authed_client,
        "Sub BOM",
        [{"part_id": main, "quantity": 1}, {"part_id": main, "quantity": 2}],
    )
    chunks: list[list[str]] = []
    original_search = __import__(
        "app.domain.sourcing.service",
        fromlist=["search"],
    ).search

    def spy_search(*args, **kwargs):
        chunks.append(list(kwargs["mpns"]))
        return original_search(*args, **kwargs)

    monkeypatch.setattr("app.domain.sourcing.service.search", spy_search)

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    assert chunks == [["MAIN-MPN", "ALT-MPN"]]
    row = r.json()["data"]["rows"][0]
    assert {offer["mpn"] for offer in row["offers"]} == {"MAIN-MPN", "ALT-MPN"}


def test_bom_over_50_mpns_chunks_correctly(authed_client, monkeypatch):
    _configure_sourcing(authed_client)
    bom = []
    for index in range(73):
        part_id = create_part(authed_client, name=f"P{index}", mpn=f"MPN-{index:02d}")
        bom.append({"part_id": part_id, "quantity": 1})
    project_id = create_project_with_bom(authed_client, "Large BOM", bom)
    chunks: list[list[str]] = []
    original_search = __import__(
        "app.domain.sourcing.service",
        fromlist=["search"],
    ).search

    def spy_search(*args, **kwargs):
        chunks.append(list(kwargs["mpns"]))
        return original_search(*args, **kwargs)

    monkeypatch.setattr("app.domain.sourcing.service.search", spy_search)

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    assert [len(chunk) for chunk in chunks] == [50, 23]
    assert len(_FakeTrustedPartsClient.calls) == 73
    ws_id = uuid.UUID(_current_workspace_id(authed_client))
    assert sum(count for _timestamp, count in BUDGET._events[(ws_id, 10)]) == 73


def test_foreign_project_returns_404(authed_client):
    other = TestClient(app)
    signup_user(other)
    _configure_sourcing(authed_client)
    foreign_project_id = _single_line_project(other, mpn="FOREIGN-MPN")

    r = _post_sourcing(authed_client, foreign_project_id)

    assert r.status_code == 404, r.text
    assert r.json()["status"]["category"] == "not_found"


def test_invalid_build_quantity_returns_422(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client)

    r = _post_sourcing(authed_client, project_id, build_quantity=0)

    assert r.status_code == 422, r.text
    assert r.json()["status"]["category"] == "validation_error"


def test_unconfigured_returns_409(authed_client, monkeypatch):
    monkeypatch.setattr(
        "app.domain.sourcing.service.make_sourcing_provider",
        lambda _workspace: None,
    )
    project_id = _single_line_project(authed_client)

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 409, r.text
    assert r.json()["status"] == {
        "category": "conflict",
        "message": "sourcing not configured",
    }


def test_budget_blocked_returns_503(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client)
    BUDGET.record(uuid.UUID(_current_workspace_id(authed_client)), 250)

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 503, r.text
    assert r.json()["status"] == {
        "category": "server_error",
        "message": "sourcing budget exhausted",
    }


def test_partial_flag_when_budget_degrades(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client)
    BUDGET.record(uuid.UUID(_current_workspace_id(authed_client)), 50)

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    assert r.json()["data"]["partial"] is True
    assert _FakeTrustedPartsClient.calls[-1]["use_cached_data"] is True


def test_risk_flag_single_source(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="SINGLE")
    _FakeTrustedPartsClient.offers_by_mpn = {"SINGLE": [_offer("SINGLE", stock=5)]}

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    assert "single_source" in r.json()["data"]["rows"][0]["risk_flags"]


def test_risk_flag_no_authorized_stock(authed_client):
    _configure_sourcing(authed_client)
    part_id = create_part(authed_client, name="No Stock", mpn="NO-STOCK")
    add_stock(authed_client, part_id, 1)
    project_id = create_project_with_bom(
        authed_client,
        "No authorized stock",
        [{"part_id": part_id, "quantity": 10}],
    )
    _FakeTrustedPartsClient.offers_by_mpn = {"NO-STOCK": [_offer("NO-STOCK", stock=0)]}

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    assert "no_authorized_stock" in r.json()["data"]["rows"][0]["risk_flags"]


def test_risk_flag_moq_overbuy(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="MOQ", quantity=5)
    _FakeTrustedPartsClient.offers_by_mpn = {"MOQ": [_offer("MOQ", stock=100, moq=100)]}

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    assert "moq_overbuy" in r.json()["data"]["rows"][0]["risk_flags"]


def test_risk_flag_lead_time_long(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="SLOW")
    _FakeTrustedPartsClient.offers_by_mpn = {"SLOW": [_offer("SLOW", stock=100, lead_time_days=45)]}

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    assert "lead_time_long" in r.json()["data"]["rows"][0]["risk_flags"]


def test_risk_flag_preferred_distributor_unmet(authed_client):
    _configure_sourcing(authed_client, preferred=["DigiKey"])
    project_id = _single_line_project(authed_client, mpn="PREF")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "PREF": [_offer("PREF", distributor="Mouser", stock=100)]
    }

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    assert "preferred_distributor_unmet" in r.json()["data"]["rows"][0]["risk_flags"]


def test_risk_flag_lifecycle_risk_present_fires_when_field_populated(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="LIFE-RISK")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "LIFE-RISK": [_offer("LIFE-RISK", lifecycle_risk="NRND")]
    }

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    assert "lifecycle_risk_present" in r.json()["data"]["rows"][0]["risk_flags"]


def test_risk_flag_lifecycle_risk_present_does_not_fire_when_field_null(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="NO-LIFE-RISK")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "NO-LIFE-RISK": [_offer("NO-LIFE-RISK", lifecycle_risk=None)]
    }

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    assert "lifecycle_risk_present" not in r.json()["data"]["rows"][0]["risk_flags"]


def test_risk_flag_supply_chain_risk_present_fires_when_field_populated(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="SUPPLY-RISK")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "SUPPLY-RISK": [_offer("SUPPLY-RISK", supply_chain_risk="Limited supply")]
    }

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    assert "supply_chain_risk_present" in r.json()["data"]["rows"][0]["risk_flags"]


def test_risk_flag_supply_chain_risk_present_does_not_fire_when_field_null(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="NO-SUPPLY-RISK")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "NO-SUPPLY-RISK": [_offer("NO-SUPPLY-RISK", supply_chain_risk=None)]
    }

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    assert "supply_chain_risk_present" not in r.json()["data"]["rows"][0]["risk_flags"]


def test_risk_flag_tariff_affected_fires_for_true(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="TARIFF-TRUE")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "TARIFF-TRUE": [_offer("TARIFF-TRUE", is_affected_by_tariff=True)]
    }

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    assert "tariff_affected" in r.json()["data"]["rows"][0]["risk_flags"]


def test_risk_flag_tariff_affected_does_not_fire_for_false_or_none(authed_client):
    _configure_sourcing(authed_client)
    project_id = create_project_with_bom(
        authed_client,
        "Tariff false or none",
        [
            {
                "part_id": create_part(authed_client, name="False tariff", mpn="TARIFF-FALSE"),
                "quantity": 1,
            },
            {
                "part_id": create_part(authed_client, name="None tariff", mpn="TARIFF-NONE"),
                "quantity": 1,
            },
        ],
    )
    _FakeTrustedPartsClient.offers_by_mpn = {
        "TARIFF-FALSE": [_offer("TARIFF-FALSE", is_affected_by_tariff=False)],
        "TARIFF-NONE": [_offer("TARIFF-NONE", is_affected_by_tariff=None)],
    }

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    rows = r.json()["data"]["rows"]
    assert all("tariff_affected" not in row["risk_flags"] for row in rows)


def test_risk_flag_rohs_non_compliant_fires_when_no_distributor_has_eu_compliant_entry(
    authed_client,
):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="ROHS-BAD")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "ROHS-BAD": [
            _offer(
                "ROHS-BAD",
                distributor="DigiKey",
                rohs_compliance=[SourcingRohsCompliance(region="EU", is_compliant=False)],
            ),
            _offer("ROHS-BAD", distributor="Mouser"),
        ]
    }

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    assert "rohs_non_compliant" in r.json()["data"]["rows"][0]["risk_flags"]


def test_risk_flag_rohs_non_compliant_does_not_fire_when_at_least_one_distributor_is_compliant(
    authed_client,
):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="ROHS-GOOD")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "ROHS-GOOD": [
            _offer("ROHS-GOOD", distributor="DigiKey"),
            _offer(
                "ROHS-GOOD",
                distributor="Mouser",
                rohs_compliance=[SourcingRohsCompliance(region="EU", is_compliant=True)],
            ),
        ]
    }

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    assert "rohs_non_compliant" not in r.json()["data"]["rows"][0]["risk_flags"]


def test_risk_flag_rohs_non_compliant_uses_eu_default_when_no_workspace_setting(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="ROHS-EU-DEFAULT")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "ROHS-EU-DEFAULT": [
            _offer(
                "ROHS-EU-DEFAULT",
                rohs_compliance=[SourcingRohsCompliance(region="US", is_compliant=True)],
            )
        ]
    }

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    assert "rohs_non_compliant" in r.json()["data"]["rows"][0]["risk_flags"]


def test_workspace_isolation_two_projects_same_mpns(authed_client):
    _configure_sourcing(authed_client)
    project_a = _single_line_project(authed_client, mpn="SHARED-MPN")

    client_b = TestClient(app)
    signup_user(client_b)
    _configure_sourcing(client_b)
    project_b = _single_line_project(client_b, mpn="SHARED-MPN")

    first = _post_sourcing(authed_client, project_a)
    second = _post_sourcing(client_b, project_b)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_offer = first.json()["data"]["rows"][0]["offers"][0]
    second_offer = second.json()["data"]["rows"][0]["offers"][0]
    assert first_offer["distributor"] != second_offer["distributor"]
    assert [call["mpn"] for call in _FakeTrustedPartsClient.calls] == [
        "SHARED-MPN",
        "SHARED-MPN",
    ]
