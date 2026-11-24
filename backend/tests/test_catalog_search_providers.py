"""SM-002 provider pieces over recorded-shape responses — no database.

`tests/fixtures/catalog_search/` holds DigiKey KeywordSearch / categories
and Mouser keyword responses: field names from DigiKey's published v4
OpenAPI definition, and which facets are null taken from recorded live
responses (sources cited in that folder's README); values illustrative. The MCP-level acceptance
tests are in `test_mcp_catalog_search.py`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.domain.parts.providers.digikey as dk
from app.domain.parts.providers import digikey_search, mouser_search
from app.domain.parts.providers.digikey import DigiKeyProvider
from app.domain.parts.providers.digikey_search import SearchQuery, build_payload
from app.domain.parts.providers.mouser import MouserProvider

FIXTURES = Path(__file__).parent / "fixtures" / "catalog_search"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(dk, "_post_token",
                        lambda cid, secret: {"access_token": "tok", "expires_in": 600})


def test_payload_carries_every_filter_digikey_accepts():
    q = SearchQuery(
        keywords="100nF 0603", category_id="60", manufacturer_ids=("490",),
        status_ids=("0",), parameter_filters=((14, ("64", "81")),),
        in_stock=True, rohs=True, has_datasheet=True, sort="-stock", limit=10, offset=20,
    )
    assert build_payload(q) == {
        "Keywords": "100nF 0603",
        "Limit": 10,
        "Offset": 20,
        "FilterOptionsRequest": {
            "CategoryFilter": [{"Id": "60"}],
            "ManufacturerFilter": [{"Id": "490"}],
            "StatusFilter": [{"Id": "0"}],
            "ParameterFilterRequest": {
                "CategoryFilter": {"Id": "60"},
                "ParameterFilters": [
                    {"ParameterId": 14, "FilterValues": [{"Id": "64"}, {"Id": "81"}]}
                ],
            },
            "SearchOptions": ["InStock", "RohsCompliant", "HasDatasheet"],
        },
        "SortOptions": {"Field": "QuantityAvailable", "SortOrder": "Descending"},
    }


def test_plain_keyword_payload_has_no_filter_block():
    assert build_payload(SearchQuery(keywords="x")) == {"Keywords": "x", "Limit": 20, "Offset": 0}


def test_digikey_search_shapes_candidates_and_facets(monkeypatch, token):
    sent = []
    broad = fixture("digikey_keyword_broad.json")
    monkeypatch.setattr(digikey_search, "_post_search",
                        lambda t, c, p: (sent.append(p), (200, broad))[1])
    out = digikey_search.search(DigiKeyProvider("cid", "s"), SearchQuery(keywords="100nF", limit=2))
    assert out["found"] is True and out["total"] == 1342
    assert len(out["candidates"]) == 2, "never more than limit"
    first = out["candidates"][0]
    assert first["mpn"] == "GRM1885C1H104JA01D"
    assert first["product_url"].startswith("https://www.digikey.cz/")
    assert first["status"] == "Active" and first["stock"] == 41230
    assert first["unit_price_qty1"] == "9.4100 CZK"
    assert first["key_parameters"]["Voltage - Rated"] == "50V"
    assert "Ratings" not in first["key_parameters"], "'-' placeholders dropped"
    assert len(first["key_parameters"]) <= digikey_search.MAX_KEY_PARAMETERS
    facets = out["facets"]
    assert facets["manufacturers"][0] == {"id": "490", "name": "Murata Electronics", "count": 312}
    assert out["applied_filters"] == sent[0]


def _search_with(monkeypatch, body, **query):
    monkeypatch.setattr(digikey_search, "_post_search", lambda t, c, p: (200, body))
    return digikey_search.search(DigiKeyProvider("cid", "s"), SearchQuery(**query))


def test_broad_search_without_top_categories_still_yields_category_ids(monkeypatch, token):
    """Regression (prod 2026-11-24): a broad DigiKey answer has TopCategories,
    Status and ParametricFilters null. The old parser returned an empty
    categories facet, leaving the agent no id to narrow with."""
    out = _search_with(monkeypatch, fixture("digikey_keyword_broad.json"),
                       keywords="ceramic capacitor 0603 C0G")
    facets = out["facets"]
    assert facets["categories"] == [
        {"id": "60", "name": "Ceramic Capacitors", "count": 3, "root": "Capacitors"}
    ]
    assert facets["categories_from"] == "candidates"
    assert facets["status"] == [] and facets["parametric_filters"] == []
    assert out["hints"] == [digikey_search.HINT_NARROW_FOR_PARAMETRICS]


def test_narrowed_search_parses_status_and_parametric_filters(monkeypatch, token):
    out = _search_with(monkeypatch, fixture("digikey_keyword_narrowed.json"),
                       keywords="ceramic capacitor 0603 C0G", category_id="60")
    facets = out["facets"]
    assert facets["status"][0] == {"id": "0", "name": "Active", "count": 11}
    by_name = {f["name"]: f for f in facets["parametric_filters"]}
    assert by_name["Capacitance"] == {
        "parameter_id": 2049, "name": "Capacitance",
        "values": [{"id": "0.1 µF", "name": "0.1 µF", "count": 12}],
    }
    assert by_name["Voltage - Rated"]["values"][0] == {"id": "64", "name": "50V", "count": 9}
    assert facets["categories"][0]["id"] == "60"
    assert "hints" not in out


def test_top_categories_are_preferred_when_digikey_sends_them(monkeypatch, token):
    # TopCategory / TopCategoryNode exactly as in Yamakaja/partrack@99201d7
    # doc/distributor/digikey/examples/keyword_search.json (live recording).
    body = fixture("digikey_keyword_broad.json")
    body["FilterOptions"]["TopCategories"] = [{
        "RootCategory": {"Id": 2, "Name": "Resistors", "ProductCount": 0},
        "Category": {"Id": 52, "Name": "Chip Resistor - Surface Mount", "ProductCount": 1},
        "Score": 0.0, "ImageUrl": "https://mm.digikey.com/x_tmb.jpg",
    }]
    facets = _search_with(monkeypatch, body, keywords="x")["facets"]
    assert facets["categories"] == [
        {"id": "52", "name": "Chip Resistor - Surface Mount", "count": 1, "root": "Resistors"}
    ]
    assert facets["categories_from"] == "top_categories"


@pytest.mark.parametrize("options", [None, {}, {"Manufacturers": None, "TopCategories": [None]}])
def test_missing_or_null_filter_options_are_tolerated(monkeypatch, token, options):
    body = {**fixture("digikey_keyword_broad.json"), "FilterOptions": options}
    facets = _search_with(monkeypatch, body, keywords="x")["facets"]
    assert facets["manufacturers"] == [] and facets["categories"][0]["id"] == "60"


@pytest.mark.parametrize("keywords,value_hint", [
    ("100nF 0603 C0G 50V", True),
    ("4.7 uH shielded", True),
    ("10pF", True),
    ("ceramic capacitor 0603 C0G 50V", False),
    ("NRF52840", False),
])
def test_zero_results_hint_about_prefixed_values(monkeypatch, token, keywords, value_hint):
    body = {"Products": [], "ProductsCount": 0, "ExactMatches": [],
            "FilterOptions": {"Manufacturers": None, "TopCategories": None}}
    out = _search_with(monkeypatch, body, keywords=keywords)
    assert out["found"] is True and out["total"] == 0
    assert (digikey_search.HINT_VALUE_KEYWORDS in out.get("hints", [])) is value_hint


@pytest.mark.parametrize("status,body,expect", [
    (403, {"ErrorMessage": "throttled"}, "suspended"),
    (429, {dk._RETRY_AFTER_KEY: "20"}, "retry_after_ms"),
])
def test_digikey_search_refusals_are_not_empty_results(monkeypatch, token, status, body, expect):
    monkeypatch.setattr(digikey_search, "_post_search", lambda t, c, p: (status, body))
    out = digikey_search.search(DigiKeyProvider("cid", "s"), SearchQuery(keywords="x"))
    assert out["found"] is False and expect in out


def test_digikey_400_detail_reaches_the_agent_with_a_leaf_category_hint(monkeypatch, token):
    body = fixture("digikey_keyword_400_parent_category.json")
    monkeypatch.setattr(digikey_search, "_post_search", lambda t, c, p: (400, body))
    out = digikey_search.search(DigiKeyProvider("cid", "s"), SearchQuery(
        keywords="capacitor", category_id="3", parameter_filters=((14, ("64",)),)))
    assert out["found"] is False and out["message"] == "DigiKey returned HTTP 400"
    assert out["provider_message"] == (
        "Parametric Filtering Not Support at Parent Category Level.")
    assert digikey_search._provider_message({"title": "Bad Request"}) == "Bad Request"
    assert out["hint"] == digikey_search.HINT_PARENT_CATEGORY
    flat = json.dumps(out)
    assert "developer.digikey.com" not in flat and "/products/v4" not in flat
    assert "0f6c1d2e" not in flat, "correlationId is not a message"


def test_provider_message_is_sanitised_and_capped():
    msg = digikey_search._provider_message({
        "detail": "bad\x00value\nsee https://api.digikey.com/x?token=abc now",
        "errors": {"Keywords": ["too\tlong", "bad\x00value see https://a.b/c now"],
                   "Limit": "x" * 400},
        "instance": "https://api.digikey.com/products/v4/search/keyword",
    })
    assert "\x00" not in msg and "\n" not in msg and "\t" not in msg
    assert "https://" not in msg and "token=abc" not in msg and "[url]" in msg
    assert msg.startswith("bad value see [url] now; too long")
    assert len(msg) == digikey_search.MAX_PROVIDER_MESSAGE and msg.endswith("…")
    assert digikey_search._provider_message("not json") is None
    assert digikey_search._provider_message({"errors": {}}) is None


@pytest.mark.parametrize("status", [401, 403, 429])
def test_auth_and_throttle_bodies_are_not_echoed(monkeypatch, token, status):
    body = {"detail": "Parametric Filtering Not Support at Parent Category Level.",
            "ErrorMessage": "throttled" if status == 403 else "nope"}
    monkeypatch.setattr(digikey_search, "_post_search", lambda t, c, p: (status, body))
    out = digikey_search.search(DigiKeyProvider("cid", "s"), SearchQuery(keywords="x"))
    assert out["found"] is False
    assert "provider_message" not in out and "hint" not in out


def test_digikey_categories_two_levels(monkeypatch, token):
    monkeypatch.setattr(digikey_search, "_get_categories",
                        lambda t, c: (200, fixture("digikey_categories.json")))
    out = digikey_search.categories(DigiKeyProvider("cid", "s"))
    caps = out["categories"][0]
    assert caps["id"] == "3" and caps["name"] == "Capacitors"
    assert {"id": "60", "name": "Ceramic Capacitors", "count": 800000} in caps["children"]


@pytest.mark.parametrize("in_stock,rohs,expected", [
    (False, False, "None"), (True, False, "InStock"),
    (False, True, "Rohs"), (True, True, "RohsAndInStock"),
])
def test_mouser_search_options(in_stock, rohs, expected):
    assert mouser_search.search_option(in_stock=in_stock, rohs=rohs) == expected


def test_mouser_keyword_search_shapes_candidates(monkeypatch):
    sent = []
    monkeypatch.setattr(mouser_search, "_post_mouser_keyword",
                        lambda url, body: (sent.append(body), fixture("mouser_keyword.json"))[1])
    out = mouser_search.search(MouserProvider("k"), keywords="100nF 0603 C0G", limit=1,
                               offset=50, in_stock=True, rohs=False)
    assert sent[0]["SearchByKeywordRequest"]["startingRecord"] == 50
    assert sent[0]["SearchByKeywordRequest"]["searchOptions"] == "InStock"
    assert out["total"] == 2204 and len(out["candidates"]) == 1
    c = out["candidates"][0]
    assert c["product_url"].startswith("https://www.mouser.cz/")
    assert c["stock"] == 61000 and c["unit_price_qty1"] == "0,38 € EUR"
    assert c["key_parameters"]["Package"] == "0603"
    assert "apiKey" not in json.dumps(out["applied_filters"])
