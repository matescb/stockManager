from __future__ import annotations

from app.domain.parts.providers.digikey import (
    DigiKeyProvider,
    _record_from_product,
)


# ---------------------------------------------------------------------------
# Synthetic ProductDetails response — covers every field we extract.
# ---------------------------------------------------------------------------


def _stub_product() -> dict:
    return {
        "ManufacturerProductNumber": "TXU0204QWBQARQ1",
        "Manufacturer": {"Id": 296, "Name": "Texas Instruments"},
        "Description": {
            "ProductDescription": "IC LVL TRANS BIDIR 4BIT 16WQFN",
            "DetailedDescription": (
                "4-bit Bidirectional Voltage-Level Translator for "
                "Open-Drain and Push-Pull Applications"
            ),
        },
        "Category": {
            "CategoryId": 1,
            "Name": "Integrated Circuits (ICs)",
            "ChildCategories": [
                {
                    "CategoryId": 2,
                    "Name": "Logic - Translators, Level Shifters",
                    "ChildCategories": [],
                }
            ],
        },
        "ProductUrl": "https://www.digikey.cz/en/products/detail/.../TXU0204QWBQARQ1/...",
        "DatasheetUrl": "https://www.ti.com/lit/ds/symlink/txu0204-q1.pdf",
        "PhotoUrl": "https://media.digikey.com/Photos/.../TXU0204QWBQARQ1.jpg",
        "QuantityAvailable": 5234,
        "ProductStatus": {"Id": 0, "Status": "Active"},
        "BackOrderNotAllowed": False,
        "Discontinued": False,
        "EndOfLife": False,
        "Ncnr": False,
        "ManufacturerLeadWeeks": "8 Weeks",
        "Series": {"Id": 0, "Name": "AutoMOTIVE"},
        "Classifications": {
            "ReachStatus": "REACH Unaffected",
            "RohsStatus": "ROHS3 Compliant",
            "MoistureSensitivityLevel": "1 (Unlimited)",
            "ExportControlClassNumber": "EAR99",
            "HtsusCode": "8542.39.00.01",
        },
        "Parameters": [
            {"ParameterText": "Translator Type", "ValueText": "Bidirectional"},
            {"ParameterText": "Channels per Circuit", "ValueText": "4"},
            {"ParameterText": "Voltage - Supply", "ValueText": "1.65 V ~ 5.5 V"},
            {"ParameterText": "Package / Case", "ValueText": "16-WQFN"},
            {"ParameterText": "Operating Temperature", "ValueText": "-40°C ~ 125°C"},
        ],
        "ProductVariations": [
            {
                "DigiKeyProductNumber": "296-TXU0204QWBQARQ1CT-ND",
                "Packaging": {"Id": 1, "Name": "Cut Tape (CT)"},
                "StandardPricing": [
                    {"BreakQuantity": 1, "UnitPrice": 27.0, "TotalPrice": 27.0},
                    {"BreakQuantity": 10, "UnitPrice": 24.5, "TotalPrice": 245.0},
                    {"BreakQuantity": 100, "UnitPrice": 19.8, "TotalPrice": 1980.0},
                ],
            },
            {
                "DigiKeyProductNumber": "296-TXU0204QWBQARQ1TR-ND",
                "Packaging": {"Id": 2, "Name": "Tape & Reel (TR)"},
                "StandardPricing": [
                    {"BreakQuantity": 3000, "UnitPrice": 18.5, "TotalPrice": 55500.0}
                ],
            },
        ],
    }


# ---------------------------------------------------------------------------
# _record_from_product — pure mapping (no HTTP).
# ---------------------------------------------------------------------------


def test_record_canonical_identity_fields():
    out = _record_from_product(_stub_product())
    assert out["mpn"] == "TXU0204QWBQARQ1"
    assert out["manufacturer"] == "Texas Instruments"
    assert out["description"] == "IC LVL TRANS BIDIR 4BIT 16WQFN"
    # Deepest category name surfaces.
    assert out["category"] == "Logic - Translators, Level Shifters"
    assert out["datasheet_url"].endswith(".pdf")
    assert out["image_url"].endswith(".jpg")
    assert out["source_url"].startswith("https://www.digikey.cz/")
    # Footprint pulled from the matching Parameter row.
    assert out["footprint"] == "16-WQFN"


def test_record_emits_every_useful_spec():
    out = _record_from_product(_stub_product())
    keys = [s["key"] for s in out["specs"]]
    by_key = {s["key"]: s["value"] for s in out["specs"]}

    # Parametric table → one row per Parameter.
    assert "Translator Type" in keys
    assert by_key["Translator Type"] == "Bidirectional"
    assert by_key["Package / Case"] == "16-WQFN"

    # Lifecycle / classifications.
    assert by_key["Lifecycle"] == "Active"
    assert by_key["RoHS"] == "ROHS3 Compliant"
    assert by_key["REACH"] == "REACH Unaffected"
    assert by_key["MSL"] == "1 (Unlimited)"
    assert by_key["HTS code"] == "8542.39.00.01"
    assert by_key["ECCN"] == "EAR99"

    # Supply.
    assert by_key["In stock (qty)"] == "5234"
    assert by_key["Lead time"] == "8 Weeks"
    assert by_key["Series"] == "AutoMOTIVE"

    # Packaging + DigiKey P/Ns collapsed.
    assert "Cut Tape (CT)" in by_key["Packaging"]
    assert "Tape & Reel (TR)" in by_key["Packaging"]
    assert "296-TXU0204QWBQARQ1CT-ND" in by_key["DigiKey P/N"]
    assert "296-TXU0204QWBQARQ1TR-ND" in by_key["DigiKey P/N"]

    # Detailed description differs from short description → its own row.
    assert "Detailed description" in by_key
    assert "Bidirectional" in by_key["Detailed description"]


def test_record_picks_cheapest_break1_pricing_source():
    out = _record_from_product(_stub_product())
    by_key = {s["key"]: s["value"] for s in out["specs"]}
    # Cut-tape variation has break-1 = 27.0; reel variation only has a 3000+
    # break at 18.5 (no break-1 row), so the CT pricing wins.
    assert "Unit price (1+)" in by_key
    assert by_key["Unit price (1+)"].startswith("27.0000")
    assert "CZK" in by_key["Unit price (1+)"]
    assert "Unit price (10+)" in by_key
    assert "Unit price (100+)" in by_key
    # The 3000+ tier from the reel variation should NOT appear because
    # we pick a single pricing source (the cheapest at break-1).
    assert "Unit price (3000+)" not in by_key


def test_record_skips_zero_quantity_and_empty_classifications():
    p = _stub_product()
    p["QuantityAvailable"] = 0
    p["Classifications"] = {"RohsStatus": "", "ReachStatus": "  "}
    out = _record_from_product(p)
    keys = [s["key"] for s in out["specs"]]
    assert "In stock (qty)" not in keys
    assert "RoHS" not in keys
    assert "REACH" not in keys


def test_record_emits_boolean_flags_when_set():
    p = _stub_product()
    p["Discontinued"] = True
    p["EndOfLife"] = True
    p["Ncnr"] = True
    out = _record_from_product(p)
    by_key = {s["key"]: s["value"] for s in out["specs"]}
    assert by_key["Discontinued"] == "yes"
    assert by_key["End of life"] == "yes"
    assert by_key["NCNR"] == "yes"


def test_record_normalizes_lead_time_without_unit():
    p = _stub_product()
    p["ManufacturerLeadWeeks"] = "12"
    out = _record_from_product(p)
    by_key = {s["key"]: s["value"] for s in out["specs"]}
    assert by_key["Lead time"] == "12 weeks"


def test_record_handles_missing_optional_blocks():
    # Smallest possible response — no Parameters, no Classifications, no
    # ProductVariations — should still produce a valid canonical record.
    minimal = {
        "ManufacturerProductNumber": "FOO",
        "Manufacturer": {"Name": "Acme"},
        "Description": {"ProductDescription": "A widget"},
        "ProductStatus": {"Status": "Active"},
        "ProductUrl": "https://example.com/foo",
    }
    out = _record_from_product(minimal)
    assert out["mpn"] == "FOO"
    assert out["manufacturer"] == "Acme"
    assert out["description"] == "A widget"
    assert out["footprint"] is None
    assert out["specs"] == [{"key": "Lifecycle", "value": "Active"}]


# ---------------------------------------------------------------------------
# End-to-end via DigiKeyProvider.lookup_mpn — patches both HTTP seams.
# ---------------------------------------------------------------------------


def _ok_token(*_args, **_kwargs):
    return {"access_token": "tok-abc", "expires_in": 600, "token_type": "Bearer"}


def test_provider_full_path_emits_canonical_record(monkeypatch):
    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._post_token", _ok_token
    )
    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._get_product_details",
        lambda token, client_id, mpn: (200, {"Product": _stub_product()}),
    )
    out = DigiKeyProvider("cid", "csec").lookup_mpn("TXU0204QWBQARQ1")
    assert out["found"] is True
    assert out["result"]["mpn"] == "TXU0204QWBQARQ1"
    assert out["result"]["manufacturer"] == "Texas Instruments"
    assert out["result"]["footprint"] == "16-WQFN"
    keys = [s["key"] for s in out["result"]["specs"]]
    assert "Translator Type" in keys
    assert "Lifecycle" in keys
    assert "Packaging" in keys


def test_provider_404_falls_back_to_keyword_search(monkeypatch):
    """ProductDetails requires exact MPN match against DigiKey's
    canonical indexing. When that 404s, we fall back to the fuzzy
    keyword search — that's how Molex bags (printed `98266-0897`)
    resolve to DigiKey's `0982660897`."""
    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._post_token", _ok_token
    )
    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._get_product_details",
        lambda token, client_id, mpn: (404, {"detail": "not found"}),
    )
    keyword_calls = {"n": 0, "kw": None}
    def keyword(token, client_id, keywords, limit=5):
        keyword_calls["n"] += 1
        keyword_calls["kw"] = keywords
        # Return a single product matching the bag-printed format to a
        # canonical 10-digit DigiKey index entry.
        product = _stub_product()
        product["ManufacturerProductNumber"] = "0982660897"
        return (200, {"Products": [product], "ProductsCount": 1})
    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._post_keyword_search", keyword
    )
    out = DigiKeyProvider("cid", "csec").lookup_mpn("98266-0897")
    assert out["found"] is True
    assert out["result"]["mpn"] == "0982660897"
    assert keyword_calls["n"] == 1
    assert keyword_calls["kw"] == "98266-0897"


def test_provider_404_with_no_keyword_hits_returns_not_found(monkeypatch):
    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._post_token", _ok_token
    )
    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._get_product_details",
        lambda token, client_id, mpn: (404, {"detail": "not found"}),
    )
    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._post_keyword_search",
        lambda token, client_id, keywords, limit=5: (200, {"Products": [], "ProductsCount": 0}),
    )
    out = DigiKeyProvider("cid", "csec").lookup_mpn("WHO-DIS")
    assert out["found"] is False
    assert out["result"] is None
    assert "no match" in (out["message"] or "")


def test_provider_429_returns_rate_limited(monkeypatch):
    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._post_token", _ok_token
    )
    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._get_product_details",
        lambda token, client_id, mpn: (429, {}),
    )
    out = DigiKeyProvider("cid", "csec").lookup_mpn("X")
    assert out["found"] is False
    assert "rate limit" in (out["message"] or "").lower()


def test_provider_auth_failure_is_caught(monkeypatch):
    def boom(*_a, **_kw):
        raise RuntimeError("bad creds")
    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._post_token", boom
    )
    out = DigiKeyProvider("cid", "csec").lookup_mpn("X")
    assert out["found"] is False
    assert "auth failed" in (out["message"] or "").lower()


def test_provider_empty_mpn_short_circuits(monkeypatch):
    # Should never even mint a token.
    def fail(*_a, **_kw):
        raise AssertionError("token endpoint should not be called")
    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._post_token", fail
    )
    out = DigiKeyProvider("cid", "csec").lookup_mpn("   ")
    assert out["found"] is False
    assert "empty" in (out["message"] or "").lower()


def test_provider_caches_token_within_instance(monkeypatch):
    calls = {"n": 0}
    def counting_token(*_a, **_kw):
        calls["n"] += 1
        return {"access_token": "tok", "expires_in": 600}
    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._post_token", counting_token
    )
    monkeypatch.setattr(
        "app.domain.parts.providers.digikey._get_product_details",
        lambda token, client_id, mpn: (200, {"Product": _stub_product()}),
    )
    p = DigiKeyProvider("cid", "csec")
    p.lookup_mpn("A")
    p.lookup_mpn("B")
    assert calls["n"] == 1


def test_provider_factory_requires_both_credentials(monkeypatch):
    from app.domain.parts.providers.base import make_provider
    # Missing client_secret → factory returns None, not a broken provider.
    assert make_provider("digikey", "client-id-only", None) is None
    assert make_provider("digikey", "client-id-only", "") is None
    p = make_provider("digikey", "cid", "csec")
    assert p is not None
    assert p.name == "digikey"
