"""`catalog_search` (SM-002) over the real MCP client.

Provider seams return the recorded-shape fixtures in
`tests/fixtures/catalog_search/`; the pacer runs on a fake clock. Test
names carry the SM-002 acceptance point they cover.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

import app.domain.parts.providers.digikey as dk
from app.domain.audit.models import AuditLog
from app.domain.custom_fields.models import CustomField
from app.domain.parts.models import Part
from app.domain.parts.providers import digikey_search, mouser_search
from app.domain.parts.services import catalog_search as search_service
from app.domain.parts.services.provider_quota import QUOTA
from app.domain.sourcing.models import SourcingCache
from tests.test_catalog_search_providers import fixture
from tests.test_mcp import _mint, call, call_error, mcp_session
from tests.test_mcp_catalog import _digikey_primary, _mouser_secondary
from tests.test_provider_quota import FakeClock


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(QUOTA, "clock", fake)
    return fake


@pytest.fixture(autouse=True)
def _clear_search_caches():
    search_service._responses._store.clear()
    search_service._categories._store.clear()
    yield
    search_service._responses._store.clear()
    search_service._categories._store.clear()


@pytest.fixture
def token(authed_client) -> str:
    return _mint(authed_client)["token"]


@pytest.fixture
def digikey(monkeypatch, authed_client) -> list[dict]:
    """DigiKey primary; returns the payloads the seam received."""
    _digikey_primary(authed_client)
    monkeypatch.setattr(dk, "_post_token",
                        lambda cid, secret: {"access_token": "tok", "expires_in": 600})
    sent: list[dict] = []

    def post(token_, client_id, payload):
        sent.append(payload)
        if payload.get("FilterOptionsRequest"):
            return 200, fixture("digikey_keyword_narrowed.json")
        if payload.get("Offset"):
            return 200, fixture("digikey_keyword_page2.json")
        return 200, fixture("digikey_keyword_broad.json")

    monkeypatch.setattr(digikey_search, "_post_search", post)
    monkeypatch.setattr(digikey_search, "_get_categories",
                        lambda t, c: (200, fixture("digikey_categories.json")))
    return sent


@pytest.fixture
def mouser(monkeypatch, authed_client) -> list[dict]:
    _mouser_secondary(authed_client)
    sent: list[dict] = []
    monkeypatch.setattr(mouser_search, "_post_mouser_keyword",
                        lambda url, body: (sent.append(body), fixture("mouser_keyword.json"))[1])
    return sent


async def test_sm002_1_keyword_search_candidates_total_facets_applied(token, digikey, clock):
    async with mcp_session(token) as s:
        out = await call(s, "catalog_search", keywords="100nF 0603 C0G", limit=2)
    assert len(out["candidates"]) == 2
    assert all(c["source"] == "digikey" and c["product_url"] and c["retrieved_at"]
               for c in out["candidates"])
    assert out["total"] == {"digikey": 1342}
    facets = out["facets"]
    assert facets["source"] == "digikey"
    assert facets["categories"][0]["id"] == "60"
    assert facets["manufacturers"][0]["count"] == 312
    assert facets["parametric_filters"] == [], "DigiKey leaves them null on a broad search"
    assert out["hints"] == [
        digikey_search.HINT_NARROW_FOR_PARAMETRICS,
        digikey_search.HINT_PACKAGE_KEYWORD.format(token="0603"),
    ]
    assert out["applied_filters"]["digikey"] == {
        "Keywords": "100nF 0603 C0G", "Limit": 2, "Offset": 0,
        "SortOptions": {"Field": "QuantityAvailable", "SortOrder": "Descending"},
    }
    assert out["applied_sort"] == {"digikey": {
        "sort": "-stock", "field": "QuantityAvailable", "order": "Descending",
        "source": "default",
    }}
    assert out["quota"][0]["spent"] == 1


async def test_sm002_1_limit_defaults_to_20_and_is_clamped_to_50(token, digikey, clock):
    async with mcp_session(token) as s:
        await call(s, "catalog_search", keywords="a")
        clock.now += 2
        await call(s, "catalog_search", keywords="b", limit=500)
    assert [p["Limit"] for p in digikey] == [20, 50]


async def test_sm002_2_requery_with_facet_ids_narrows(token, digikey, clock):
    async with mcp_session(token) as s:
        broad = await call(s, "catalog_search", keywords="100nF 0603 C0G")
        cat = broad["facets"]["categories"][0]["id"]
        clock.now += 2
        by_category = await call(s, "catalog_search", keywords="100nF 0603 C0G",
                                 category_id=cat)
        volt = next(f for f in by_category["facets"]["parametric_filters"]
                    if f["name"] == "Voltage - Rated")
        clock.now += 2
        narrow = await call(
            s, "catalog_search", keywords="100nF 0603 C0G", category_id=cat,
            manufacturer_ids=[broad["facets"]["manufacturers"][0]["id"]],
            parameter_filters=[{"parameter_id": volt["parameter_id"],
                                "value_ids": [volt["values"][0]["id"]]}],
            in_stock=True,
        )
    assert narrow["total"]["digikey"] < broad["total"]["digikey"]
    sent = digikey[-1]["FilterOptionsRequest"]
    assert sent["CategoryFilter"] == [{"Id": "60"}]
    assert sent["ParameterFilterRequest"]["ParameterFilters"] == [
        {"ParameterId": 14, "FilterValues": [{"Id": "64"}]}
    ]
    assert sent["SearchOptions"] == ["InStock"]
    assert narrow["applied_filters"]["digikey"] == digikey[-1]


async def test_sm002_3_parameter_filters_for_mouser_are_refused(token, digikey, mouser, clock):
    async with mcp_session(token) as s:
        message = await call_error(
            s, "catalog_search", keywords="100nF", provider="mouser", category_id="60",
            parameter_filters=[{"parameter_id": 14, "value_ids": ["64"]}],
        )
    assert "provider.unsupported_filter" in message
    assert mouser == [], "refused before spending a Mouser call"


async def test_sm002_3_with_all_providers_mouser_is_listed_not_silently_dropped(
    token, digikey, mouser, clock
):
    async with mcp_session(token) as s:
        out = await call(s, "catalog_search", keywords="100nF", category_id="60")
    assert {c["source"] for c in out["candidates"]} == {"digikey"}
    assert out["errors"] == [{
        "provider": "mouser", "code": "provider.unsupported_filter",
        "message": "mouser supports keyword search only (with in_stock / rohs); not category_id",
    }]
    assert mouser == []


async def test_both_providers_side_by_side_never_merged(token, digikey, mouser, clock):
    async with mcp_session(token) as s:
        out = await call(s, "catalog_search", keywords="100nF 0603 C0G", limit=2,
                         in_stock=True)
    sources = [c["source"] for c in out["candidates"]]
    # in_stock sends a filter block, so DigiKey answers with the narrowed
    # fixture (one product); grouped per provider, DigiKey first.
    assert sources == ["digikey", "mouser", "mouser"]
    assert out["total"] == {"digikey": 12, "mouser": 2204}
    assert mouser[0]["SearchByKeywordRequest"]["searchOptions"] == "InStock"
    assert "apiKey" not in json.dumps(out) and "fake-mouser-key" not in json.dumps(out)
    # Mouser takes no sort: its own order, said so rather than left blank.
    assert out["applied_sort"]["mouser"] == {"sort": None, "source": "provider"}
    assert out["applied_sort"]["digikey"]["source"] == "default"


async def test_digikey_candidates_say_price_and_stock_are_indicative(
        token, digikey, mouser, clock):
    async with mcp_session(token) as s:
        out = await call(s, "catalog_search", keywords="100nF 0603 C0G", limit=2,
                         in_stock=True)
    by_source = {c["source"]: c for c in out["candidates"]}
    assert by_source["digikey"]["price_stock_freshness"] == "indicative_up_to_24h"
    # Mouser states no figure: left out rather than guessed.
    assert "price_stock_freshness" not in by_source["mouser"]


async def test_description_tells_agents_to_confirm_with_catalog_lookup(token):
    async with mcp_session(token) as s:
        tools = {t.name: t for t in (await s.list_tools()).tools}
    text = tools["catalog_search"].description
    assert "indicative" in text and "24 h" in text and "catalog_lookup" in text


async def test_an_explicit_sort_is_sent_as_asked_and_reported(token, digikey, clock):
    async with mcp_session(token) as s:
        out = await call(s, "catalog_search", keywords="100nF 0603 C0G", sort="price")
    assert digikey[-1]["SortOptions"] == {"Field": "Price", "SortOrder": "Ascending"}
    assert out["applied_sort"]["digikey"] == {
        "sort": "price", "field": "Price", "order": "Ascending", "source": "caller",
    }


async def test_sm002_4_nothing_is_written_or_flushed(token, digikey, mouser, clock, db):
    """After the call no part, spec, audit or cache row exists, and no
    session flushed any catalogue object."""
    def counts():
        return [db.scalar(select(func.count()).select_from(m))
                for m in (Part, CustomField, AuditLog)]

    flushed: list[str] = []

    def on_flush(session, flush_context, instances):
        flushed.extend(type(o).__name__ for o in (*session.new, *session.dirty, *session.deleted))

    before = counts()
    sourcing_before = db.scalar(select(func.count()).select_from(SourcingCache))
    event.listen(Session, "before_flush", on_flush)
    try:
        async with mcp_session(token) as s:
            await call(s, "catalog_search", keywords="100nF 0603 C0G", list_categories=True)
    finally:
        event.remove(Session, "before_flush", on_flush)
    assert counts() == before
    assert db.scalar(select(func.count()).select_from(SourcingCache)) == sourcing_before
    assert not [name for name in flushed if name in {"Part", "CustomField", "AuditLog",
                                                    "SourcingCache", "Attachment"}], flushed


async def test_sm002_5_offset_pages_through_results(token, digikey, clock):
    async with mcp_session(token) as s:
        first = await call(s, "catalog_search", keywords="100nF 0603 C0G", limit=3)
        clock.now += 2
        second = await call(s, "catalog_search", keywords="100nF 0603 C0G", limit=3, offset=3)
    assert digikey[-1]["Offset"] == 3 and second["offset"] == 3
    first_mpns = {c["mpn"] for c in first["candidates"]}
    assert first_mpns.isdisjoint({c["mpn"] for c in second["candidates"]})


async def test_read_only_token_is_refused(authed_client, digikey):
    readonly = _mint(authed_client, read_only=True)["token"]
    async with mcp_session(readonly) as s:
        assert "auth.token_read_only" in await call_error(s, "catalog_search", keywords="x")
    assert digikey == []


async def test_identical_digikey_search_is_served_from_the_short_cache(token, digikey, clock):
    async with mcp_session(token) as s:
        await call(s, "catalog_search", keywords="100nF")
        again = await call(s, "catalog_search", keywords="100nF")
    assert again["cache"] == "hit" and again["quota"][0]["spent"] == 0
    assert len(digikey) == 1


async def test_mouser_is_never_cached(token, mouser, clock):
    async with mcp_session(token) as s:
        await call(s, "catalog_search", keywords="100nF", provider="mouser")
        clock.now += 2
        again = await call(s, "catalog_search", keywords="100nF", provider="mouser")
    assert again["cache"] == "miss" and len(mouser) == 2


async def test_list_categories_returns_digikeys_tree(token, digikey, clock):
    async with mcp_session(token) as s:
        out = await call(s, "catalog_search", list_categories=True)
    assert out["categories"][0]["name"] == "Capacitors"
    assert out["candidates"] == [] and digikey == [], "no keywords, no search call"


async def test_parameter_filters_need_a_category(token, digikey, clock):
    async with mcp_session(token) as s:
        message = await call_error(s, "catalog_search", keywords="x",
                                   parameter_filters=[{"parameter_id": 14, "value_ids": ["64"]}])
    assert "provider.invalid_filter" in message


async def test_second_search_within_the_spacing_is_paced(token, digikey, clock):
    async with mcp_session(token) as s:
        await call(s, "catalog_search", keywords="a")
        message = await call_error(s, "catalog_search", keywords="b")
    assert "provider.pacing" in message


async def test_a_suspended_digikey_is_reported_not_empty(token, digikey, monkeypatch, clock):
    monkeypatch.setattr(digikey_search, "_post_search",
                        lambda t, c, p: (403, {"ErrorMessage": "burst limit, throttled"}))
    async with mcp_session(token) as s:
        message = await call_error(s, "catalog_search", keywords="a")
    assert "provider.suspended" in message


async def test_digikey_400_detail_and_hint_reach_the_agent(token, digikey, monkeypatch, clock):
    body = fixture("digikey_keyword_400_parent_category.json")
    monkeypatch.setattr(digikey_search, "_post_search", lambda t, c, p: (400, body))
    async with mcp_session(token) as s:
        message = await call_error(
            s, "catalog_search", keywords="capacitor", category_id="3", provider="digikey",
            parameter_filters=[{"parameter_id": 14, "value_ids": ["64"]}])
    assert "provider.upstream_error" in message
    assert "Parametric Filtering Not Support at Parent Category Level" in message
    assert digikey_search.HINT_PARENT_CATEGORY in message
    assert "developer.digikey.com" not in message and "/products/v4" not in message


async def test_digikey_400_detail_is_on_its_errors_entry_beside_mouser(
        token, digikey, mouser, monkeypatch, clock):
    body = fixture("digikey_keyword_400_parent_category.json")
    monkeypatch.setattr(digikey_search, "_post_search", lambda t, c, p: (400, body))
    async with mcp_session(token) as s:
        out = await call(s, "catalog_search", keywords="capacitor")
    error = next(e for e in out["errors"] if e["provider"] == "digikey")
    assert error["provider_message"].startswith(
        "Parametric Filtering Not Support at Parent Category Level")
    assert error["hint"] == digikey_search.HINT_PARENT_CATEGORY
    assert out["candidates"] and all(c["source"] == "mouser" for c in out["candidates"])
