from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.core.ratelimit as _ratelimit_mod
from app.core.secrets import decrypt
from app.domain.sourcing import SourcingAuthError
from app.domain.sourcing.schemas import SourcingQuery
from app.domain.workspaces.models import Workspace, WorkspaceMember
from app.main import app


def _signup(client: TestClient | None = None) -> tuple[TestClient, str]:
    c = client or TestClient(app)
    r = c.post(
        "/api/auth/signup",
        json={
            "email": f"u-{uuid.uuid4().hex[:8]}@example.com",
            "name": "Tester",
            "password": "TestPass-2026-Stronk",
        },
    )
    assert r.status_code == 200, r.text
    return c, r.json()["data"]["workspace_id"]


def _configure_sourcing(
    client: TestClient,
    *,
    company_id: str = "company-123",
    api_key: str = "api-key-456",
) -> None:
    r = client.patch(
        "/api/workspaces/current",
        json={
            "sourcing_provider": "trustedparts",
            "sourcing_company_id": company_id,
            "sourcing_api_key": api_key,
            "sourcing_country_code": "CZ",
            "sourcing_currency_code": "EUR",
        },
    )
    assert r.status_code == 200, r.text


class _SuccessfulTrustedPartsClient:
    calls: list[dict] = []

    def __init__(
        self,
        company_id: str,
        api_key: str,
        country_code: str | None,
        currency_code: str | None,
        user_agent: str,
    ) -> None:
        self.company_id = company_id
        self.api_key = api_key
        self.country_code = country_code
        self.currency_code = currency_code
        self.user_agent = user_agent

    def search(
        self,
        queries: list[SourcingQuery],
        *,
        use_cached_data: bool,
        **_kwargs,
    ):
        assert all(isinstance(query, SourcingQuery) for query in queries)
        self.calls.append(
            {
                "company_id": self.company_id,
                "api_key": self.api_key,
                "country_code": self.country_code,
                "currency_code": self.currency_code,
                "user_agent": self.user_agent,
                "queries": [query.model_dump(exclude_none=True) for query in queries],
                "use_cached_data": use_cached_data,
            }
        )
        return object()


@pytest.fixture(autouse=False)
def limiter_enabled():
    original = _ratelimit_mod.limiter.enabled
    _ratelimit_mod.limiter.enabled = True
    try:
        _ratelimit_mod.limiter.reset()
    except Exception:
        pass
    yield
    _ratelimit_mod.limiter.enabled = original
    try:
        _ratelimit_mod.limiter.reset()
    except Exception:
        pass


def test_unconfigured_returns_not_configured(authed_client):
    r = authed_client.post("/api/workspaces/current/sourcing/test")

    assert r.status_code == 200, r.text
    assert r.json() == {
        "data": {"ok": False, "message": "not configured", "latency_ms": 0},
        "status": {"category": "ok", "message": "OK"},
    }


def test_bad_creds_returns_invalid_credentials(authed_client, monkeypatch):
    _configure_sourcing(authed_client)

    class BadCredsTrustedPartsClient(_SuccessfulTrustedPartsClient):
        def search(self, queries: list[dict], *, use_cached_data: bool, **_kwargs):
            raise SourcingAuthError("bad credentials")

    monkeypatch.setattr(
        "app.domain.sourcing.factory.TrustedPartsClient",
        BadCredsTrustedPartsClient,
    )

    r = authed_client.post("/api/workspaces/current/sourcing/test")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"]["category"] == "ok"
    assert body["data"]["ok"] is False
    assert body["data"]["message"] == "invalid credentials"
    assert body["data"]["latency_ms"] > 0


def test_good_creds_returns_ok_and_latency(authed_client, monkeypatch):
    _configure_sourcing(authed_client)
    _SuccessfulTrustedPartsClient.calls = []
    monkeypatch.setattr(
        "app.domain.sourcing.factory.TrustedPartsClient",
        _SuccessfulTrustedPartsClient,
    )

    r = authed_client.post("/api/workspaces/current/sourcing/test")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["data"]["ok"] is True
    assert body["data"]["message"] == "OK"
    assert body["data"]["latency_ms"] > 0
    assert _SuccessfulTrustedPartsClient.calls == [
        {
            "company_id": "",
            "api_key": "api-key-456",
            "country_code": "CZ",
            "currency_code": "EUR",
            "user_agent": f"stockManager/dev workspace={_current_workspace_id(authed_client)}",
            "queries": [{"search_token": "TEST_PROBE_DO_NOT_BUY"}],
            "use_cached_data": False,
        }
    ]


def test_rate_limit_after_6_per_minute(authed_client, monkeypatch, limiter_enabled):
    _configure_sourcing(authed_client)
    monkeypatch.setattr(
        "app.domain.sourcing.factory.TrustedPartsClient",
        _SuccessfulTrustedPartsClient,
    )

    for _ in range(6):
        r = authed_client.post("/api/workspaces/current/sourcing/test")
        assert r.status_code == 200, r.text

    r = authed_client.post("/api/workspaces/current/sourcing/test")
    assert r.status_code == 429, r.text
    body = r.json()
    assert body["data"] is None
    assert body["status"]["category"] == "rate_limited"
    assert body["retry_after_seconds"] > 0


def test_non_admin_forbidden(authed_client, db):
    ws_id = _current_workspace_id(authed_client)
    membership = db.execute(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == ws_id)
    ).scalar_one()
    membership.role = "member"
    db.flush()

    r = authed_client.post("/api/workspaces/current/sourcing/test")

    assert r.status_code == 403, r.text
    body = r.json()
    assert body["data"] is None
    assert body["status"]["category"] == "forbidden"


def test_workspace_isolation_uses_own_creds(db, monkeypatch):
    client_a, ws_a_id = _signup()
    _configure_sourcing(
        client_a,
        company_id="company-a",
        api_key="api-key-a",
    )
    client_b, ws_b_id = _signup()
    _configure_sourcing(
        client_b,
        company_id="company-b",
        api_key="api-key-b",
    )

    ws_a = db.get(Workspace, ws_a_id)
    ws_b = db.get(Workspace, ws_b_id)
    assert ws_a is not None
    assert ws_b is not None
    a_tokens = {ws_a.sourcing_api_key_enc}
    b_tokens = {ws_b.sourcing_api_key_enc}
    seen_tokens: list[str | None] = []

    def decrypt_spy(token: str | None) -> str | None:
        seen_tokens.append(token)
        return decrypt(token)

    _SuccessfulTrustedPartsClient.calls = []
    monkeypatch.setattr("app.domain.sourcing.factory.decrypt", decrypt_spy)
    monkeypatch.setattr(
        "app.domain.sourcing.factory.TrustedPartsClient",
        _SuccessfulTrustedPartsClient,
    )

    r = client_b.post("/api/workspaces/current/sourcing/test")

    assert r.status_code == 200, r.text
    assert r.json()["data"]["ok"] is True
    assert set(seen_tokens) == b_tokens
    assert not set(seen_tokens) & a_tokens
    assert _SuccessfulTrustedPartsClient.calls[-1]["company_id"] == ""
    assert _SuccessfulTrustedPartsClient.calls[-1]["api_key"] == "api-key-b"


def _current_workspace_id(client: TestClient) -> str:
    r = client.get("/api/workspaces/current")
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]
