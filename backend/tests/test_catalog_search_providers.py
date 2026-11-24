"""SM-002 provider pieces over recorded-shape responses — no database.

`tests/fixtures/catalog_search/` holds DigiKey KeywordSearch / categories
and Mouser keyword responses (field names from the v4 and Mouser schemas,
illustrative values; see that folder's README). The MCP-level acceptance
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
    assert facets["categories"] == [
        {"id": "60", "name": "Ceramic Capacitors", "count": 1342, "root": "Capacitors"}
    ]
    assert facets["manufacturers"][0] == {"id": "490", "name": "Murata Electronics", "count": 312}
    voltage = next(f for f in facets["parametric_filters"] if f["parameter_id"] == 14)
    assert voltage["values"][0] == {"id": "64", "name": "50V", "count": 640}
    assert out["applied_filters"] == sent[0]


@pytest.mark.parametrize("status,body,expect", [
    (403, {"ErrorMessage": "throttled"}, "suspended"),
    (429, {dk._RETRY_AFTER_KEY: "20"}, "retry_after_ms"),
])
def test_digikey_search_refusals_are_not_empty_results(monkeypatch, token, status, body, expect):
    monkeypatch.setattr(digikey_search, "_post_search", lambda t, c, p: (status, body))
    out = digikey_search.search(DigiKeyProvider("cid", "s"), SearchQuery(keywords="x"))
    assert out["found"] is False and expect in out


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
