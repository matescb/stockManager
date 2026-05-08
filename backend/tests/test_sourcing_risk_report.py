from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

import app.core.ratelimit as _ratelimit_mod
from app.domain.sourcing.budget import BUDGET
from app.domain.sourcing.schemas import (
    SourcingDistributor,
    SourcingLinks,
    SourcingOffer,
    SourcingPriceBreak,
    SourcingQuery,
    SourcingSearchRaw,
)
from app.infra.db import Base
from app.main import app
from tests._factories import add_stock, create_part, create_storage, signup_user


def _configure_sourcing(
    client: TestClient,
    *,
    preferred: list[str] | None = None,
    use_cached: bool = True,
) -> None:
    r = client.patch(
        "/api/workspaces/current",
        json={
            "sourcing_provider": "trustedparts",
            "sourcing_company_id": "company-123",
            "sourcing_api_key": "api-key-456",
            "sourcing_country_code": "CZ",
            "sourcing_currency_code": "EUR",
            "sourcing_preferred_distributors": preferred or [],
            "sourcing_use_cached_for_dashboards": use_cached,
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

    def __init__(self, workspace: Any) -> None:
        self.workspace = workspace
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
                "workspace_id": str(self.workspace.id),
                "mpn": query.search_token,
                "distributors": distributors,
                "use_cached_data": use_cached_data,
            }
        )
        return SourcingSearchRaw(
            offers=self.offers_by_mpn.get(query.search_token, []),
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

    def fake_provider(workspace):
        if workspace.sourcing_provider != "trustedparts":
            return None
        return _FakeTrustedPartsClient(workspace)

    monkeypatch.setattr("app.domain.sourcing.service.make_sourcing_provider", fake_provider)
    yield
    _ratelimit_mod.limiter.enabled = original_limiter_enabled
    BUDGET._events.clear()
    try:
        _ratelimit_mod.limiter.reset()
    except Exception:
        pass


def _report(client: TestClient, *, only_with_flags: bool = True):
    r = client.get(f"/api/reports/sourcing-risk?only_with_flags={str(only_with_flags).lower()}")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_single_source_flag(authed_client):
    _configure_sourcing(authed_client)
    create_part(authed_client, name="Single", mpn="SINGLE")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "SINGLE": [_offer("SINGLE", distributor="DigiKey", stock=25)]
    }

    row = _report(authed_client)["rows"][0]

    assert row["risk_flags"] == ["single_source"]
    assert row["distributors_with_stock"] == ["DigiKey"]


def test_no_authorized_stock_flag(authed_client):
    _configure_sourcing(authed_client)
    part_id = create_part(authed_client, name="On hand unavailable", mpn="NOAUTH")
    storage_id = create_storage(authed_client)
    add_stock(authed_client, part_id, 3, storage_id)
    _FakeTrustedPartsClient.offers_by_mpn = {"NOAUTH": [_offer("NOAUTH", stock=0)]}

    row = _report(authed_client)["rows"][0]

    assert row["on_hand"] == 3
    assert row["risk_flags"] == ["no_authorized_stock"]


def test_moq_overbuy_uses_workspace_threshold(authed_client):
    _configure_sourcing(authed_client)
    create_part(authed_client, name="MOQ", mpn="MOQ", low_stock_report_quantity=20)
    _FakeTrustedPartsClient.offers_by_mpn = {"MOQ": [_offer("MOQ", stock=1000, moq=101)]}

    row = _report(authed_client)["rows"][0]

    assert row["typical_reorder_quantity"] == 20
    assert "moq_overbuy" in row["risk_flags"]


def test_lead_time_long_threshold(authed_client):
    _configure_sourcing(authed_client)
    create_part(authed_client, name="Lead", mpn="LEAD")
    _FakeTrustedPartsClient.offers_by_mpn = {"LEAD": [_offer("LEAD", stock=50, lead_time_days=31)]}

    row = _report(authed_client)["rows"][0]

    assert "lead_time_long" in row["risk_flags"]


def test_preferred_distributor_unmet(authed_client):
    _configure_sourcing(authed_client, preferred=["DigiKey"])
    create_part(authed_client, name="Preferred", mpn="PREF")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "PREF": [_offer("PREF", distributor="Mouser", stock=20)]
    }

    row = _report(authed_client)["rows"][0]

    assert row["distributors_with_stock"] == ["Mouser"]
    assert "preferred_distributor_unmet" in row["risk_flags"]


def test_price_delta_25pct(authed_client):
    _configure_sourcing(authed_client)
    part_id = create_part(authed_client, name="Price", mpn="PRICE")
    storage_id = create_storage(authed_client)
    add_stock(
        authed_client,
        part_id,
        10,
        storage_id,
        price={"mode": "per_component", "unit_price": "1.00", "currency": "EUR"},
        lot={"name": "purchase"},
    )
    _FakeTrustedPartsClient.offers_by_mpn = {"PRICE": [_offer("PRICE", stock=10, unit_price=1.25)]}

    row = _report(authed_client)["rows"][0]

    assert row["historical_unit_cost"] == "1.000000"
    assert row["price_delta_pct"] == "0.25"
    assert "price_delta" in row["risk_flags"]


def test_only_with_flags_filters_clean_parts(authed_client):
    _configure_sourcing(authed_client)
    create_part(authed_client, name="Risky", mpn="RISKY")
    create_part(authed_client, name="Clean", mpn="CLEAN")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "RISKY": [_offer("RISKY", distributor="DigiKey", stock=10)],
        "CLEAN": [
            _offer("CLEAN", distributor="DigiKey", stock=10),
            _offer("CLEAN", distributor="Mouser", stock=10),
        ],
    }

    flagged = _report(authed_client, only_with_flags=True)["rows"]
    all_rows = _report(authed_client, only_with_flags=False)["rows"]

    assert [row["mpn"] for row in flagged] == ["RISKY"]
    assert {row["mpn"] for row in all_rows} == {"RISKY", "CLEAN"}


def test_sort_by_flag_count_then_alpha(authed_client):
    _configure_sourcing(authed_client, preferred=["DigiKey"])
    create_part(authed_client, name="Bravo", mpn="BRAVO")
    create_part(authed_client, name="Alpha", mpn="ALPHA")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "BRAVO": [_offer("BRAVO", distributor="Mouser", stock=10, lead_time_days=31)],
        "ALPHA": [_offer("ALPHA", distributor="Mouser", stock=10)],
    }

    rows = _report(authed_client)["rows"]

    assert [row["name"] for row in rows] == ["Bravo", "Alpha"]
    assert len(rows[0]["risk_flags"]) == 3
    assert len(rows[1]["risk_flags"]) == 2


def test_workspace_isolation(authed_client):
    _configure_sourcing(authed_client)
    create_part(authed_client, name="Own", mpn="OWN")
    other = TestClient(app)
    signup_user(other)
    _configure_sourcing(other)
    create_part(other, name="Foreign", mpn="FOREIGN")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "OWN": [_offer("OWN", stock=10)],
        "FOREIGN": [_offer("FOREIGN", stock=10)],
    }

    rows = _report(authed_client)["rows"]

    assert [row["mpn"] for row in rows] == ["OWN"]
    assert {call["workspace_id"] for call in _FakeTrustedPartsClient.calls} == {
        str(_current_workspace_id(authed_client))
    }


def test_cache_hit_within_4h(authed_client):
    _configure_sourcing(authed_client, use_cached=False)
    create_part(authed_client, name="Cached", mpn="CACHE")
    _FakeTrustedPartsClient.offers_by_mpn = {"CACHE": [_offer("CACHE", stock=10)]}

    first = _report(authed_client)
    second = _report(authed_client)

    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert len(_FakeTrustedPartsClient.calls) == 1
    assert _FakeTrustedPartsClient.calls[0]["use_cached_data"] is True


def test_sourcing_not_configured_returns_status(authed_client):
    create_part(authed_client, name="Unconfigured", mpn="UNCONFIGURED")

    data = _report(authed_client)

    assert data["sourcing_status"]["state"] == "not_configured"
    assert data["rows"] == []


def test_no_persistent_risk_table(db):
    table_names = set(inspect(db.bind).get_table_names())
    metadata_tables = set(Base.metadata.tables)

    assert not any("sourcing_risk" in name or "risk_history" in name for name in table_names)
    assert not any("sourcing_risk" in name or "risk_history" in name for name in metadata_tables)
