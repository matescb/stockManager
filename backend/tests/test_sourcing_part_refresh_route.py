from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import app.core.ratelimit as _ratelimit_mod
from app.domain.sourcing.budget import BUDGET
from app.domain.sourcing.models import SourcingCache
from app.domain.sourcing.schemas import (
    SourcingDistributor,
    SourcingLinks,
    SourcingOffer,
    SourcingQuery,
    SourcingSearchRaw,
)
from app.main import app
from tests._factories import create_part, signup_user


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


def _current_workspace_id(client: TestClient) -> uuid.UUID:
    r = client.get("/api/workspaces/current")
    assert r.status_code == 200, r.text
    return uuid.UUID(r.json()["data"]["id"])


def _cache_row_count(db, workspace_id: uuid.UUID) -> int:
    return db.execute(
        select(func.count())
        .select_from(SourcingCache)
        .where(SourcingCache.workspace_id == workspace_id)
    ).scalar_one()


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


def test_refresh_bypasses_existing_cache_row(authed_client):
    _configure_sourcing(authed_client)
    part_id = create_part(authed_client, name="STM32", mpn="STM32F103C8T6")

    cached = authed_client.get(f"/api/parts/{part_id}/sourcing")
    assert cached.status_code == 200, cached.text
    assert cached.json()["data"]["cache_hit"] is False
    assert len(_FakeTrustedPartsClient.calls) == 1

    _FakeTrustedPartsClient.calls = []
    refreshed = authed_client.post(f"/api/parts/{part_id}/sourcing/refresh")

    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["data"]["cache_hit"] is False
    assert _FakeTrustedPartsClient.calls == [
        {
            "queries": [{"search_token": "STM32F103C8T6"}],
            "country_code": "CZ",
            "currency_code": "EUR",
            "in_stock_only": False,
            "distributors": ["DigiKey"],
            "use_cached_data": False,
        }
    ]


def test_refresh_replaces_cache_row_not_duplicates(authed_client, db):
    _configure_sourcing(authed_client)
    workspace_id = _current_workspace_id(authed_client)
    part_id = create_part(authed_client, name="BAV99", mpn="BAV99")

    cached = authed_client.get(f"/api/parts/{part_id}/sourcing")
    assert cached.status_code == 200, cached.text
    assert _cache_row_count(db, workspace_id) == 1

    refreshed = authed_client.post(f"/api/parts/{part_id}/sourcing/refresh")

    assert refreshed.status_code == 200, refreshed.text
    assert _cache_row_count(db, workspace_id) == 1
    row = db.execute(
        select(SourcingCache).where(SourcingCache.workspace_id == workspace_id)
    ).scalar_one()
    assert row.response_json["request_id"] == "req-2"


def test_part_without_mpn_returns_422(authed_client):
    part_id = create_part(authed_client, name="Manual part")

    r = authed_client.post(f"/api/parts/{part_id}/sourcing/refresh")

    assert r.status_code == 422, r.text
    body = r.json()
    assert body["data"] is None
    assert body["code"] == "part_missing_mpn"
    assert body["status"] == {
        "category": "validation_error",
        "message": "part has no MPN",
    }
    assert _FakeTrustedPartsClient.calls == []


def test_foreign_part_returns_404(authed_client):
    foreign_client = TestClient(app)
    signup_user(foreign_client)
    foreign_part_id = create_part(foreign_client, name="Foreign", mpn="BAT54C")

    r = authed_client.post(f"/api/parts/{foreign_part_id}/sourcing/refresh")

    assert r.status_code == 404, r.text
    body = r.json()
    assert body["data"] is None
    assert body["code"] == "resource.not_found"
    assert body["status"]["category"] == "not_found"


def test_rate_limit_at_6_per_minute(authed_client):
    _ratelimit_mod.limiter.enabled = True
    _configure_sourcing(authed_client)
    part_id = create_part(authed_client, name="MAX232", mpn="MAX232")

    for _index in range(6):
        r = authed_client.post(f"/api/parts/{part_id}/sourcing/refresh")
        assert r.status_code == 200, r.text

    r = authed_client.post(f"/api/parts/{part_id}/sourcing/refresh")

    assert r.status_code == 429, r.text
    assert r.json()["status"]["category"] == "rate_limited"


def test_budget_block_returns_503(authed_client):
    _configure_sourcing(authed_client)
    workspace_id = _current_workspace_id(authed_client)
    part_id = create_part(authed_client, name="BAT54", mpn="BAT54C")
    BUDGET.record(workspace_id, 250)

    r = authed_client.post(f"/api/parts/{part_id}/sourcing/refresh")

    assert r.status_code == 503, r.text
    assert r.json()["code"] == "budget_exhausted"
    assert r.json()["status"] == {
        "category": "server_error",
        "message": "sourcing budget exhausted",
    }
    assert _FakeTrustedPartsClient.calls == []


def test_refresh_consumes_budget(authed_client):
    _configure_sourcing(authed_client)
    workspace_id = _current_workspace_id(authed_client)
    part_id = create_part(authed_client, name="LM358", mpn="LM358")

    before = BUDGET._window_total(workspace_id, 10)
    r = authed_client.post(f"/api/parts/{part_id}/sourcing/refresh")

    assert r.status_code == 200, r.text
    assert BUDGET._window_total(workspace_id, 10) == before + 1
