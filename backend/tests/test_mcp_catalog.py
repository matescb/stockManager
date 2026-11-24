"""`catalog_lookup` (SM-001) and the agent-path quota rules (SM-005) over MCP,
plus the `fetch_lcsc` gate.

Driven through the real MCP client like `test_mcp.py`, with the provider
network seams stubbed the way `test_secondary_provider.py` stubs them and
the pacer given a fake clock so the spacing can be stepped over. Each test names the
acceptance point it covers (edaflow `modules/stockmanager.md`).
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import app.domain.parts.providers.digikey as dk
from app.domain.audit.models import AuditLog
from app.domain.custom_fields.models import CustomField
from app.domain.parts.models import Part
from app.domain.parts.services.provider_quota import QUOTA, account_key
from tests.test_mcp import _mint, call, call_error, mcp_session
from tests.test_provider_quota import FakeClock

MPN = "OPA192IDBVT"
DK_URL = "https://www.digikey.cz/en/products/detail/texas-instruments/OPA192IDBVT/5428698"
DK_ACCOUNT = account_key("fake-client-id")


def _dk_product(mpn: str = MPN) -> dict:
    return {
        "ManufacturerProductNumber": mpn,
        "Manufacturer": {"Name": "Texas Instruments"},
        "Description": {"ProductDescription": "IC OPAMP GP 1 CIRCUIT SOT23-5"},
        "Category": {"Name": "Integrated Circuits (ICs)"},
        "ProductUrl": DK_URL,
        "DatasheetUrl": "https://www.ti.com/lit/ds/symlink/opa192.pdf",
        "QuantityAvailable": 5234,
        "ProductStatus": {"Status": "Active"},
        "Classifications": {"RohsStatus": "ROHS3 Compliant", "MoistureSensitivityLevel": "1"},
        "ProductVariations": [
            {"StandardPricing": [
                {"BreakQuantity": 1, "UnitPrice": 52.1},
                {"BreakQuantity": 10, "UnitPrice": 41.5},
            ]}
        ],
    }


def _mouser_part(manufacturer: str, mpn: str = MPN) -> dict:
    return {
        "ManufacturerPartNumber": mpn,
        "Manufacturer": manufacturer,
        "Description": "Op amp",
        "ProductDetailUrl": f"https://www.mouser.com/ProductDetail/{manufacturer}/{mpn}",
    }


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(QUOTA, "clock", fake)
    return fake


@pytest.fixture
def token(authed_client) -> str:
    return _mint(authed_client)["token"]


def _digikey_primary(c: TestClient) -> None:
    r = c.patch("/api/workspaces/current", json={
        "parts_provider": "digikey",
        "parts_provider_api_key": "fake-client-id",
        "parts_provider_api_secret": "fake-client-secret",
    })
    assert r.status_code == 200, r.text


def _mouser_secondary(c: TestClient) -> None:
    r = c.put("/api/workspaces/current/provider-credentials",
              json={"provider": "mouser", "api_key": "fake-mouser-key"})
    assert r.status_code == 200, r.text


_THROTTLED = {"ErrorMessage": "Your application has been throttled; burst limit exceeded"}


def _stub_digikey(monkeypatch, *, status: int = 200, product: dict | None = None,
                  remaining: str | None = None, error_body: dict | None = None) -> list[str]:
    """Stub the seams; `remaining` plays the X-RateLimit-Remaining header."""
    calls: list[str] = []
    monkeypatch.setattr(dk, "_post_token",
                        lambda cid, secret: {"access_token": "tok", "expires_in": 600})

    def details(token, client_id, mpn):
        calls.append(mpn)
        if remaining is not None:
            QUOTA.note_remaining("digikey", account_key(client_id),
                                 {"X-RateLimit-Remaining": remaining})
        if status == 200:
            return status, {"Product": product or _dk_product(mpn)}
        return status, error_body or {}

    monkeypatch.setattr(dk, "_get_product_details", details)
    return calls


def _stub_mouser(monkeypatch, parts: list[dict]) -> None:
    payload = {"Errors": [], "SearchResults": {"Parts": parts}}
    monkeypatch.setattr("app.domain.parts.providers.mouser._post_mouser", lambda u, b: payload)


# ---------------------------------------------------------------------------
# SM-001
# ---------------------------------------------------------------------------
async def test_sm001_1_exact_record_with_source_and_product_url(
    authed_client, token, monkeypatch, clock
):
    _digikey_primary(authed_client)
    _stub_digikey(monkeypatch)
    async with mcp_session(token) as s:
        out = await call(s, "catalog_lookup", mpn=MPN)
    assert out["match"] == "exact"
    [rec] = out["records"]
    assert rec["source"] == "digikey" and rec["match"] == "exact"
    assert rec["product_url"] == DK_URL, "the provider's own link, unmodified"
    assert rec["retrieved_at"] and rec["mpn"] == MPN
    assert rec["manufacturer"] == "Texas Instruments"
    assert rec["lifecycle"] == {"status": "Active"}
    assert rec["compliance"] == {"RoHS": "ROHS3 Compliant", "MSL": "1"}
    assert rec["stock"] == 5234
    assert [b["qty"] for b in rec["price_breaks"]] == [1, 10]
    assert out["cache"] == "miss"


async def test_sm001_2_keyword_fallback_is_fuzzy_never_exact(
    authed_client, token, monkeypatch, clock
):
    _digikey_primary(authed_client)
    _stub_digikey(monkeypatch, status=404)
    monkeypatch.setattr(
        dk, "_post_keyword_search",
        lambda t, c, k, limit=5: (200, {"Products": [_dk_product("0982660897")]}),
    )
    async with mcp_session(token) as s:
        out = await call(s, "catalog_lookup", mpn="98266-0897")
    assert out["match"] == "fuzzy"
    assert out["records"][0]["match"] == "fuzzy"
    assert out["records"][0]["mpn"] == "0982660897", "the MPN actually found"


async def test_sm001_3_several_mouser_manufacturers_come_back_as_candidates(
    authed_client, token, monkeypatch, clock
):
    _digikey_primary(authed_client)
    _mouser_secondary(authed_client)
    _stub_mouser(monkeypatch, [_mouser_part("Texas Instruments"), _mouser_part("Rochester")])
    async with mcp_session(token) as s:
        out = await call(s, "catalog_lookup", mpn=MPN, provider="mouser",
                         manufacturer="Rochester")
    makers = {c["manufacturer"] for c in out["candidates"]}
    assert makers == {"Texas Instruments", "Rochester"}
    assert all(c["match"] == "exact" and c["source"] == "mouser" for c in out["candidates"])
    [rec] = out["records"]
    assert rec["manufacturer"] == "Rochester" and rec["manufacturer_matches"] is True


async def test_sm001_4_sm005_1_read_only_token_is_refused(authed_client, monkeypatch):
    _digikey_primary(authed_client)
    calls = _stub_digikey(monkeypatch)
    readonly = _mint(authed_client, read_only=True)["token"]
    async with mcp_session(readonly) as s:
        message = await call_error(s, "catalog_lookup", mpn=MPN)
    assert "auth.token_read_only" in message
    assert calls == [], "refused before any quota was spent"


async def test_sm001_5_cache_hit_spends_no_quota_and_says_so(
    authed_client, token, monkeypatch, clock
):
    _digikey_primary(authed_client)
    calls = _stub_digikey(monkeypatch)
    async with mcp_session(token) as s:
        first = await call(s, "catalog_lookup", mpn=MPN)
        second = await call(s, "catalog_lookup", mpn=MPN)
    assert (first["cache"], first["quota"][0]["spent"]) == ("miss", 1)
    assert (second["cache"], second["quota"][0]["spent"]) == ("hit", 0)
    assert second["records"][0]["retrieved_at"] == first["records"][0]["retrieved_at"]
    assert calls == [MPN], "the second answer never reached DigiKey"
    # A cache hit is not paced either: the second call came 0 s after the
    # first and was not refused with provider.pacing.


async def test_sm001_lookup_writes_nothing(authed_client, token, monkeypatch, clock, db):
    _digikey_primary(authed_client)
    _stub_digikey(monkeypatch)

    def counts():
        return [
            db.scalar(select(func.count()).select_from(m))
            for m in (Part, CustomField, AuditLog)
        ]

    before = counts()
    async with mcp_session(token) as s:
        await call(s, "catalog_lookup", mpn=MPN)
    assert counts() == before


async def test_no_provider_configured_is_a_clean_refusal(token):
    async with mcp_session(token) as s:
        message = await call_error(s, "catalog_lookup", mpn=MPN)
    assert "part.provider_not_configured" in message


# ---------------------------------------------------------------------------
# SM-005
# ---------------------------------------------------------------------------
async def test_sm005_2_every_answer_carries_a_quota_object(
    authed_client, token, monkeypatch, clock
):
    _digikey_primary(authed_client)
    _mouser_secondary(authed_client)
    _stub_digikey(monkeypatch, remaining="700")
    _stub_mouser(monkeypatch, [_mouser_part("Texas Instruments")])
    async with mcp_session(token) as s:
        out = await call(s, "catalog_lookup", mpn=MPN)
    assert [r["source"] for r in out["records"]] == ["digikey", "mouser"], "side by side"
    by_provider = {q["provider"]: q for q in out["quota"]}
    assert by_provider["digikey"] == {
        "provider": "digikey", "spent": 1, "remaining_day": 700,
        "reserve": 200, "source": "header",
    }
    assert by_provider["mouser"]["source"] == "local-count"
    assert by_provider["mouser"]["remaining_day"] == 999


async def test_sm005_3_below_reserve_refuses_the_agent_but_not_the_ui(
    authed_client, token, monkeypatch, clock
):
    _digikey_primary(authed_client)
    calls = _stub_digikey(monkeypatch)
    QUOTA.note_remaining("digikey", DK_ACCOUNT, {"X-RateLimit-Remaining": "150"})
    async with mcp_session(token) as s:
        message = await call_error(s, "catalog_lookup", mpn=MPN)
    assert "provider.quota_reserve_reached" in message
    assert "remaining_day=150" in message and "reserve=200" in message
    assert calls == []

    # The owner's own lookup is not held to the reserve.
    r = authed_client.post("/api/parts/lookup-mpn", json={"mpn": MPN})
    assert r.status_code == 200 and r.json()["data"]["found"] is True
    assert calls == [MPN]


async def test_sm005_3_one_provider_at_reserve_does_not_silence_the_other(
    authed_client, token, monkeypatch, clock
):
    _digikey_primary(authed_client)
    _mouser_secondary(authed_client)
    _stub_digikey(monkeypatch)
    _stub_mouser(monkeypatch, [_mouser_part("Texas Instruments")])
    QUOTA.note_remaining("digikey", DK_ACCOUNT, {"X-RateLimit-Remaining": "10"})
    async with mcp_session(token) as s:
        out = await call(s, "catalog_lookup", mpn=MPN)
    assert [r["source"] for r in out["records"]] == ["mouser"]
    assert out["errors"][0]["code"] == "provider.quota_reserve_reached"


async def test_sm005_4_a_live_call_within_the_spacing_is_refused_with_retry_after(
    authed_client, token, monkeypatch, clock
):
    _digikey_primary(authed_client)
    calls = _stub_digikey(monkeypatch)
    async with mcp_session(token) as s:
        await call(s, "catalog_lookup", mpn=MPN)
        clock.now += 0.4
        refused = await call_error(s, "catalog_lookup", mpn="OPA2192IDR")
        clock.now += 0.6
        again = await call(s, "catalog_lookup", mpn="OPA2192IDR")
    assert "provider.pacing" in refused and "retry_after_ms=600" in refused
    assert again["records"][0]["mpn"] == "OPA2192IDR"
    assert calls == [MPN, "OPA2192IDR"], "the refused call never reached DigiKey"


async def test_sm005_4_pacing_with_all_providers_lists_retry_after_ms(
    authed_client, token, monkeypatch, clock
):
    _digikey_primary(authed_client)
    _mouser_secondary(authed_client)
    _stub_digikey(monkeypatch)
    _stub_mouser(monkeypatch, [_mouser_part("Texas Instruments", "OPA2192IDR")])
    async with mcp_session(token) as s:
        await call(s, "catalog_lookup", mpn=MPN, provider="digikey")
        out = await call(s, "catalog_lookup", mpn="OPA2192IDR")
    assert [r["source"] for r in out["records"]] == ["mouser"]
    [err] = out["errors"]
    assert err["code"] == "provider.pacing" and err["retry_after_ms"] == 1000


async def test_sm005_5_digikey_403_is_suspended_not_no_match(
    authed_client, token, monkeypatch, clock
):
    _digikey_primary(authed_client)
    calls = _stub_digikey(monkeypatch, status=403, error_body=_THROTTLED)
    async with mcp_session(token) as s:
        first = await call_error(s, "catalog_lookup", mpn=MPN)
        clock.now += 5  # past the pacing slot; the breaker still holds
        second = await call_error(s, "catalog_lookup", mpn="OPA2192IDR")
    assert "provider.suspended" in first and "no match" not in first
    assert "provider.suspended" in second and "retry in" in second
    assert calls == [MPN], "the open breaker kept the second call off the network"


async def test_sm005_5_suspension_in_one_workspace_spares_another(
    authed_client, monkeypatch, clock
):
    """Cross-tenant: breakers are per credential, not per provider name."""
    from app.main import app
    from tests._factories import signup_user

    _digikey_primary(authed_client)
    other = TestClient(app)
    signup_user(other)
    r = other.patch("/api/workspaces/current", json={
        "parts_provider": "digikey",
        "parts_provider_api_key": "other-client-id",
        "parts_provider_api_secret": "other-secret",
    })
    assert r.status_code == 200, r.text
    monkeypatch.setattr(dk, "_post_token",
                        lambda cid, secret: {"access_token": "tok", "expires_in": 600})

    def details(token_, client_id, mpn):
        if client_id == "fake-client-id":
            return 403, _THROTTLED
        return 200, {"Product": _dk_product(mpn)}

    monkeypatch.setattr(dk, "_get_product_details", details)
    async with mcp_session(_mint(authed_client)["token"]) as s:
        assert "provider.suspended" in await call_error(s, "catalog_lookup", mpn=MPN)
    async with mcp_session(_mint(other)["token"]) as s:
        out = await call(s, "catalog_lookup", mpn="OPA2192IDR")
    assert out["records"][0]["source"] == "digikey"


async def test_a_bare_403_is_a_credential_error_not_suspended(
    authed_client, token, monkeypatch, clock
):
    _digikey_primary(authed_client)
    _stub_digikey(monkeypatch, status=403, error_body={"ErrorMessage": "Unauthorized client"})
    async with mcp_session(token) as s:
        message = await call_error(s, "catalog_lookup", mpn=MPN)
    assert "provider.upstream_error" in message and "credentials" in message
    assert "provider.suspended" not in message


# ---------------------------------------------------------------------------
# fetch_lcsc gate
# ---------------------------------------------------------------------------
async def test_fetch_lcsc_is_refused_while_parked(authed_client, token, monkeypatch):
    from app.domain.eda import lcsc

    def boom(_):
        raise AssertionError("EasyEDA must not be contacted")

    monkeypatch.setattr(lcsc, "fetch_plan", boom)
    async with mcp_session(token) as s:
        message = await call_error(s, "fetch_lcsc", part_id="anything", lcsc_id="C25804")
    assert "eda.lcsc_disabled" in message


async def test_fetch_lcsc_works_again_when_enabled(authed_client, token, monkeypatch):
    from app.core.config import settings
    from app.domain.eda import lcsc
    from tests._factories import create_part

    monkeypatch.setattr(settings(), "MCP_FETCH_LCSC_ENABLED", True)

    def not_found(_):
        raise lcsc.LcscNotFound("C25804")

    monkeypatch.setattr(lcsc, "fetch_plan", not_found)
    create_part(authed_client, "LCSC gated", mpn="LCSC-GATE-1")
    async with mcp_session(token) as s:
        message = await call_error(s, "fetch_lcsc", part_id="LCSC-GATE-1", lcsc_id="C25804")
    assert "eda.lcsc_not_found" in message


# ---------------------------------------------------------------------------
# Review follow-ups
# ---------------------------------------------------------------------------
async def test_another_workspaces_lookup_is_not_visible_as_a_cache_hit(
    authed_client, monkeypatch, clock
):
    """The shared provider cache is keyed provider:mpn across workspaces.
    `cache: "hit"`, `spent: 0` or a borrowed `retrieved_at` would tell B
    that A looked this MPN up, and when."""
    from app.main import app
    from tests._factories import signup_user

    _digikey_primary(authed_client)
    other = TestClient(app)
    signup_user(other)
    _digikey_primary(other)
    calls = _stub_digikey(monkeypatch)

    async with mcp_session(_mint(authed_client)["token"]) as s:
        a = await call(s, "catalog_lookup", mpn=MPN)
    clock.now += 5
    async with mcp_session(_mint(other)["token"]) as s:
        b = await call(s, "catalog_lookup", mpn=MPN)
        b_again = await call(s, "catalog_lookup", mpn=MPN)
    assert a["cache"] == "miss"
    assert b["cache"] == "miss" and b["quota"][0]["spent"] == 1
    assert b["records"][0]["retrieved_at"] != a["records"][0]["retrieved_at"]
    assert calls == [MPN, MPN], "B's first lookup went to DigiKey itself"
    assert b_again["cache"] == "hit", "B's own lookup is cached for B"


async def test_an_unexpected_http_error_keeps_the_other_provider_and_hides_the_url(
    authed_client, token, monkeypatch, clock
):
    _digikey_primary(authed_client)
    _mouser_secondary(authed_client)
    _stub_digikey(monkeypatch)

    def broken(url, body):
        raise httpx.RemoteProtocolError(f"peer closed connection: {url}")

    monkeypatch.setattr("app.domain.parts.providers.mouser._post_mouser", broken)
    async with mcp_session(token) as s:
        out = await call(s, "catalog_lookup", mpn=MPN)
    assert [r["source"] for r in out["records"]] == ["digikey"]
    [err] = out["errors"]
    assert err["code"] == "provider.upstream_error"
    assert "fake-mouser-key" not in json.dumps(out) and "apiKey" not in json.dumps(out)


async def test_a_digikey_429_is_returned_with_retry_after_ms(
    authed_client, token, monkeypatch, clock
):
    _digikey_primary(authed_client)
    _mouser_secondary(authed_client)
    _stub_digikey(monkeypatch, status=429, error_body={dk._RETRY_AFTER_KEY: "12"})
    _stub_mouser(monkeypatch, [_mouser_part("Texas Instruments")])
    async with mcp_session(token) as s:
        out = await call(s, "catalog_lookup", mpn=MPN)
    [err] = out["errors"]
    assert err["code"] == "rate_limited" and err["retry_after_ms"] == 12000
    assert [r["source"] for r in out["records"]] == ["mouser"]
