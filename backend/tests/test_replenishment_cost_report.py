from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from app.domain.sourcing.models import SourcingCache
from app.domain.sourcing.schemas import (
    SourcingDistributor,
    SourcingLinks,
    SourcingOffer,
    SourcingQuery,
    SourcingSearchRaw,
)
from app.main import app
from tests._factories import add_stock, create_part, create_storage, signup_user


class _FakeTrustedPartsClient:
    calls: list[dict[str, Any]] = []
    offers_by_mpn: dict[str, dict[str, Any] | None] = {}

    def __init__(self) -> None:
        self.country_code = "CZ"
        self.currency_code = "EUR"

    @classmethod
    def reset(cls) -> None:
        cls.calls = []
        cls.offers_by_mpn = {}

    def search(
        self,
        queries: list[SourcingQuery],
        *,
        in_stock_only: bool,
        distributors: list[str] | None,
        use_cached_data: bool,
        **_kwargs,
    ) -> SourcingSearchRaw:
        self.calls.append(
            {
                "queries": [query.search_token for query in queries],
                "use_cached_data": use_cached_data,
                "in_stock_only": in_stock_only,
                "distributors": distributors,
            }
        )
        offers = []
        for query in queries:
            spec = self.offers_by_mpn.get(query.search_token)
            if spec is None:
                continue
            offers.append(
                SourcingOffer(
                    mpn=query.search_token,
                    manufacturer="ReportCo",
                    distributors=[
                        SourcingDistributor(
                            name="DigiKey",
                            sku=f"{query.search_token}-DK",
                            stock=100,
                            unit_price=spec["unit_price"],
                            currency=spec["currency"],
                            product_url=f"https://www.trustedparts.com/{query.search_token}",
                        )
                    ],
                    links=SourcingLinks(
                        primary=f"https://www.trustedparts.com/search/{query.search_token}"
                    ),
                )
            )
        return SourcingSearchRaw(offers=offers, request_id=f"req-{len(self.calls)}")


@pytest.fixture(autouse=True)
def _fake_sourcing(monkeypatch):
    _FakeTrustedPartsClient.reset()
    monkeypatch.setattr(
        "app.domain.sourcing.service.make_sourcing_provider",
        lambda _workspace: _FakeTrustedPartsClient(),
    )
    yield
    _FakeTrustedPartsClient.reset()


def _authed_client(email_prefix: str = "replenishment") -> TestClient:
    client = TestClient(app)
    signup_user(client, email=f"{email_prefix}-{uuid.uuid4().hex[:8]}@example.com")
    return client


def _configure_sourcing(client: TestClient) -> None:
    r = client.patch(
        "/api/workspaces/current",
        json={
            "sourcing_provider": "trustedparts",
            "sourcing_company_id": "company-123",
            "sourcing_api_key": "api-key-456",
            "sourcing_country_code": "CZ",
            "sourcing_currency_code": "EUR",
            "sourcing_preferred_distributors": ["DigiKey"],
            "sourcing_use_cached_for_dashboards": False,
        },
    )
    assert r.status_code == 200, r.text


def _stocked_part(
    client: TestClient,
    *,
    name: str,
    mpn: str,
    quantity: int = 10,
    unit_cost: str = "0.50",
    currency: str = "EUR",
) -> str:
    part_id = create_part(client, name=name, mpn=mpn)
    storage_id = create_storage(client, name=f"{name} bin")
    add_stock(
        client,
        part_id,
        quantity,
        storage_id=storage_id,
        lot_name=f"{name} lot",
        price={
            "mode": "per_component",
            "unit_price": unit_cost,
            "currency": currency,
        },
    )
    return part_id


def _report(client: TestClient, query: str = "") -> dict[str, Any]:
    r = client.get(f"/api/reports/replenishment-cost{query}")
    assert r.status_code == 200, r.text
    assert r.json()["status"]["category"] == "ok"
    return r.json()["data"]


def test_basic_report_with_matching_currency():
    client = _authed_client()
    _configure_sourcing(client)
    part_id = _stocked_part(client, name="Cap", mpn="CAP-10UF")
    _FakeTrustedPartsClient.offers_by_mpn = {"CAP-10UF": {"unit_price": 0.75, "currency": "EUR"}}

    data = _report(client)

    assert data["sourcing_status"]["state"] == "ok"
    row = data["rows"][0]
    assert row["part_id"] == part_id
    assert row["on_hand"] == 10
    assert row["historical_cost"] == "5.000000"
    assert row["replacement_cost"] == "7.50"
    assert row["delta_abs"] == "2.500000"
    assert row["delta_pct"] == "50.00"
    assert row["source"] == "trustedparts"


def test_currency_mismatch_returns_null_delta_with_reason():
    client = _authed_client()
    _configure_sourcing(client)
    _stocked_part(client, name="Reg", mpn="REG-3V3", currency="USD")
    _FakeTrustedPartsClient.offers_by_mpn = {"REG-3V3": {"unit_price": 1.25, "currency": "EUR"}}

    row = _report(client)["rows"][0]

    assert row["replacement_currency"] == "EUR"
    assert row["delta_abs"] is None
    assert row["delta_pct"] is None
    assert row["reason"] == "currency_mismatch"


def test_part_without_offer_returns_null_replacement():
    client = _authed_client()
    _configure_sourcing(client)
    _stocked_part(client, name="No offer", mpn="NO-OFFER")
    _FakeTrustedPartsClient.offers_by_mpn = {"NO-OFFER": None}

    row = _report(client)["rows"][0]

    assert row["replacement_cost"] is None
    assert row["delta_abs"] is None
    assert row["reason"] == "no_offer"


def test_no_persistent_price_history_table(engine):
    inspector = inspect(engine)
    schema_names = set(inspector.get_table_names())
    for table_name in inspector.get_table_names():
        schema_names.update(column["name"] for column in inspector.get_columns(table_name))
    forbidden_fragments = {
        "replenishment_cost",
        "price_history",
        "price_snapshot",
        "trustedparts_price",
    }

    assert not any(fragment in name for name in schema_names for fragment in forbidden_fragments)


def test_sort_by_delta_pct():
    client = _authed_client()
    _configure_sourcing(client)
    _stocked_part(client, name="Low delta", mpn="LOW", unit_cost="1.00")
    _stocked_part(client, name="High delta", mpn="HIGH", unit_cost="1.00")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "LOW": {"unit_price": 1.10, "currency": "EUR"},
        "HIGH": {"unit_price": 2.00, "currency": "EUR"},
    }

    rows = _report(client, "?sort=delta_pct")["rows"]

    assert [row["mpn"] for row in rows] == ["HIGH", "LOW"]


def test_workspace_isolation():
    a = _authed_client("a-replenishment")
    b = _authed_client("b-replenishment")
    _configure_sourcing(a)
    _configure_sourcing(b)
    part_a = _stocked_part(a, name="A private", mpn="A-PRIVATE")
    part_b = _stocked_part(b, name="B private", mpn="B-PRIVATE")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "A-PRIVATE": {"unit_price": 1.00, "currency": "EUR"},
        "B-PRIVATE": {"unit_price": 1.00, "currency": "EUR"},
    }

    rows_a = _report(a)["rows"]
    rows_b = _report(b)["rows"]

    assert [row["part_id"] for row in rows_a] == [part_a]
    assert [row["part_id"] for row in rows_b] == [part_b]


def test_not_configured_status_flag(monkeypatch):
    client = _authed_client()
    _stocked_part(client, name="No config", mpn="NO-CONFIG")
    monkeypatch.setattr(
        "app.domain.sourcing.service.make_sourcing_provider",
        lambda _workspace: None,
    )

    data = _report(client)

    assert data["sourcing_status"]["state"] == "not_configured"
    assert data["rows"][0]["replacement_cost"] is None
    assert data["rows"][0]["reason"] == "sourcing_not_configured"


def test_cache_hit_within_4h(db):
    client = _authed_client()
    _configure_sourcing(client)
    _stocked_part(client, name="Cached", mpn="CACHE-1")
    _FakeTrustedPartsClient.offers_by_mpn = {"CACHE-1": {"unit_price": 0.75, "currency": "EUR"}}

    first = _report(client)
    second = _report(client)

    assert first["sourcing_status"]["cache_hit"] is False
    assert second["sourcing_status"]["cache_hit"] is True
    assert len(_FakeTrustedPartsClient.calls) == 1
    cache_row = db.execute(select(SourcingCache)).scalar_one()
    assert int((cache_row.expires_at - cache_row.fetched_at).total_seconds()) == 4 * 60 * 60
