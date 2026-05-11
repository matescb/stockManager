from __future__ import annotations

import uuid
from datetime import datetime, timezone

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


def _signup(client: TestClient | None = None) -> tuple[TestClient, str]:
    c = client or TestClient(app)
    r = c.post(
        "/api/auth/signup",
        json={
            "email": f"sourcing-{uuid.uuid4().hex[:8]}@example.com",
            "name": "Sourcing Tester",
            "password": "TestPass-2026-Stronk",
        },
    )
    assert r.status_code == 200, r.text
    return c, r.json()["data"]["workspace_id"]


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
            tp_current_date=datetime(2026, 5, 10, 12, tzinfo=timezone.utc),
            tp_response_time="00:00:01.234",
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


def test_envelope_shape(authed_client):
    _configure_sourcing(authed_client)

    r = authed_client.post("/api/sourcing/search", json={"mpns": ["STM32F103C8T6"]})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"]["category"] == "ok"
    data = body["data"]
    assert data["powered_by"] == "TrustedParts"
    datetime.fromisoformat(data["fetched_at"])
    assert data["links"]["primary"]
    assert data["request_id"] == "req-1"
    assert datetime.fromisoformat(data["tp_current_date"]) == datetime(
        2026,
        5,
        10,
        12,
        tzinfo=timezone.utc,
    )
    assert data["tp_response_time"] == "00:00:01.234"
    assert data["cache_hit"] is False
    assert data["results"][0]["mpn"] == "STM32F103C8T6"
    assert datetime.fromisoformat(data["results"][0]["tp_current_date"]) == datetime(
        2026,
        5,
        10,
        12,
        tzinfo=timezone.utc,
    )
    assert data["results"][0]["tp_response_time"] == "00:00:01.234"
    assert data["results"][0]["cache_hit"] is False


def test_too_few_or_too_many_mpns_422(authed_client):
    _configure_sourcing(authed_client)

    r = authed_client.post("/api/sourcing/search", json={"mpns": []})
    assert r.status_code == 422, r.text
    assert r.json()["status"]["category"] == "validation_error"

    r = authed_client.post(
        "/api/sourcing/search",
        json={"mpns": [f"MPN-{index}" for index in range(51)]},
    )
    assert r.status_code == 422, r.text
    assert r.json()["status"]["category"] == "validation_error"


def test_short_search_token_returns_422_without_provider_call(authed_client):
    _configure_sourcing(authed_client)

    r = authed_client.post("/api/sourcing/search", json={"mpns": ["x"]})

    assert r.status_code == 422, r.text
    assert r.json()["status"]["category"] == "validation_error"
    assert "SearchToken" in r.json()["status"]["message"]
    assert _FakeTrustedPartsClient.calls == []


def test_unconfigured_returns_409(authed_client, monkeypatch):
    monkeypatch.setattr(
        "app.domain.sourcing.service.make_sourcing_provider",
        lambda _workspace: None,
    )

    r = authed_client.post("/api/sourcing/search", json={"mpns": ["BAT54C"]})

    assert r.status_code == 409, r.text
    assert r.json()["code"] == "workspace_not_configured"
    assert r.json()["status"] == {
        "category": "conflict",
        "message": "sourcing not configured",
    }


def test_budget_blocked_returns_503(authed_client):
    _configure_sourcing(authed_client)
    ws_id = _current_workspace_id(authed_client)
    BUDGET.record(uuid.UUID(ws_id), 250)

    r = authed_client.post("/api/sourcing/search", json={"mpns": ["BAT54C"]})

    assert r.status_code == 503, r.text
    assert r.json()["code"] == "budget_exhausted"
    assert r.json()["status"] == {
        "category": "server_error",
        "message": "sourcing budget exhausted",
    }


def test_rate_limited_returns_429_after_60_per_minute(authed_client):
    _ratelimit_mod.limiter.enabled = True
    _configure_sourcing(authed_client)

    for _index in range(60):
        r = authed_client.post("/api/sourcing/search", json={"mpns": ["MAX232"]})
        assert r.status_code == 200, r.text

    r = authed_client.post("/api/sourcing/search", json={"mpns": ["MAX232"]})

    assert r.status_code == 429, r.text
    assert r.json()["status"]["category"] == "rate_limited"
    assert r.json()["code"] == "rate_limited"


def test_cache_hit_reflected_in_response(authed_client):
    _configure_sourcing(authed_client)

    first = authed_client.post("/api/sourcing/search", json={"mpns": ["BAV99"]})
    second = authed_client.post("/api/sourcing/search", json={"mpns": ["BAV99"]})

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["data"]["cache_hit"] is False
    assert second.json()["data"]["cache_hit"] is True
    assert second.json()["data"]["results"][0]["cache_hit"] is True
    assert len(_FakeTrustedPartsClient.calls) == 1


def test_attribution_links_present(authed_client):
    _configure_sourcing(authed_client)

    r = authed_client.post("/api/sourcing/search", json={"mpns": ["NE555"]})

    assert r.status_code == 200, r.text
    links = r.json()["data"]["links"]
    assert links["primary"] == "https://www.trustedparts.com/"
    assert links["attribution"] == "https://www.trustedparts.com/en/about"


def test_degraded_mode_forces_use_cached_data_true(authed_client):
    _configure_sourcing(authed_client)
    ws_id = _current_workspace_id(authed_client)
    BUDGET.record(uuid.UUID(ws_id), 50)

    r = authed_client.post(
        "/api/sourcing/search",
        json={"mpns": ["LM358"], "use_cached_data": False},
    )

    assert r.status_code == 200, r.text
    assert _FakeTrustedPartsClient.calls[-1]["use_cached_data"] is True


def _current_workspace_id(client: TestClient) -> str:
    r = client.get("/api/workspaces/current")
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]
