from __future__ import annotations

import uuid
from datetime import datetime
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
    SourcingSearchRaw,
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


def _post_sourcing(client: TestClient, project_id: str, build_quantity: int = 1):
    return client.post(
        f"/api/projects/{project_id}/sourcing",
        json={"build_quantity": build_quantity},
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
    row = data["rows"][0]
    assert row["required"] == 20
    assert row["available"] == 0
    assert row["short_by"] == 20
    assert row["authorized_stock"] == 60
    assert row["best_offer"]["distributor"] == "Mouser"
    assert isinstance(row["best_offer"]["unit_price"], str)
    assert isinstance(row["est_extended_cost"], str)


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
    _FakeTrustedPartsClient.offers_by_mpn = {
        "NO-STOCK": [_offer("NO-STOCK", stock=0)]
    }

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    assert "no_authorized_stock" in r.json()["data"]["rows"][0]["risk_flags"]


def test_risk_flag_moq_overbuy(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="MOQ", quantity=5)
    _FakeTrustedPartsClient.offers_by_mpn = {
        "MOQ": [_offer("MOQ", stock=100, moq=100)]
    }

    r = _post_sourcing(authed_client, project_id)

    assert r.status_code == 200, r.text
    assert "moq_overbuy" in r.json()["data"]["rows"][0]["risk_flags"]


def test_risk_flag_lead_time_long(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="SLOW")
    _FakeTrustedPartsClient.offers_by_mpn = {
        "SLOW": [_offer("SLOW", stock=100, lead_time_days=45)]
    }

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
