from __future__ import annotations

from app.domain.parts.providers.mouser import MouserProvider, parse_description_specs


# ---------------------------------------------------------------------------
# parse_description_specs — heuristic on the Description string
# ---------------------------------------------------------------------------


def test_yageo_resistor_description():
    desc = (
        "Thick Film Resistors - SMD General Purpose Chip Resistor 0402, "
        "0Ohms, 5%, 1/16W"
    )
    out = dict(parse_description_specs(desc))
    assert out["Package"] == "0402"
    assert out["Resistance"].lower().endswith("ohms") or "Ω" in out["Resistance"]
    assert "5" in out["Tolerance"]
    assert out["Power"].endswith("W")


def test_capacitor_with_voltage_and_capacitance():
    desc = "Multilayer Ceramic Capacitors MLCC - SMD/SMT 0805, 100nF, 50V, 10%"
    out = dict(parse_description_specs(desc))
    assert out["Package"] == "0805"
    assert out["Capacitance"] == "100nF"
    assert "50" in out["Voltage"]
    assert "10" in out["Tolerance"]


def test_inductor():
    desc = "Inductors - SMD 1210, 10uH, 1A"
    out = dict(parse_description_specs(desc))
    assert out["Package"] == "1210"
    assert "uH" in out["Inductance"] or "μH" in out["Inductance"]
    assert "A" in out["Current"]


def test_microcontroller_yields_nothing():
    # MCU descriptions are unlabelled marketing copy; we'd rather emit
    # zero rows than mislabel "32BIT" as something nonsensical.
    desc = "ARM Microcontrollers - MCU 32BIT Cortex M3 64KB 20KB RAM 2X12 ADC"
    out = dict(parse_description_specs(desc))
    assert out == {} or "Package" not in out


def test_oscillator_frequency_and_voltage():
    desc = "Standard Clock Oscillators - SMD SOIC-8 16MHz 3.3V"
    out = dict(parse_description_specs(desc))
    assert "Package" in out
    assert "SOIC-8" in out["Package"]


def test_empty_description():
    assert parse_description_specs("") == []
    assert parse_description_specs(None) == []


def test_no_dash_prefix_still_parses():
    desc = "0805, 4.7uF, 25V"
    out = dict(parse_description_specs(desc))
    assert "Capacitance" in out
    assert "Voltage" in out
    assert out["Package"] == "0805"


def test_dedups_first_match_wins():
    desc = "Resistors - 0402, 0.1W, 0.25W"
    out = dict(parse_description_specs(desc))
    assert out["Power"] == "0.1W"


# ---------------------------------------------------------------------------
# End-to-end: MouserProvider.lookup_mpn pulls direct fields + description
# ---------------------------------------------------------------------------


def _stub_full_response() -> dict:
    return {
        "Errors": [],
        "SearchResults": {
            "NumberOfResult": 1,
            "Parts": [
                {
                    "Manufacturer": "YAGEO",
                    "ManufacturerPartNumber": "RC0402JR-070R",
                    "Description": (
                        "Thick Film Resistors - SMD General Purpose Chip "
                        "Resistor 0402, 0Ohms, 5%, 1/16W"
                    ),
                    "Category": "Thick Film Resistors - SMD",
                    "DataSheetUrl": "https://example.com/ds.pdf",
                    "ImagePath": "https://example.com/img.jpg",
                    "ProductDetailUrl": "https://www.mouser.com/ProductDetail/...",
                    "LifecycleStatus": "Production",
                    "ROHSStatus": "RoHS Compliant",
                    "Availability": "5,000 In Stock",
                    "LeadTime": "85 Days",
                    "Min": "1",
                    "Mult": "1",
                    "MouserPartNumber": "603-RC0402JR-070R",
                    "ActualMfrName": "YAGEO Phycomp",
                    "PriceBreaks": [
                        {"Quantity": 1,    "Price": "$0.10",    "Currency": "USD"},
                        {"Quantity": 100,  "Price": "$0.05",    "Currency": "USD"},
                        {"Quantity": 1000, "Price": "$0.02",    "Currency": "USD"},
                    ],
                    "ProductAttributes": [
                        {"AttributeName": "Packaging", "AttributeValue": "Reel"},
                        {"AttributeName": "Packaging", "AttributeValue": "Cut Tape"},
                        {"AttributeName": "Standard Pack Qty", "AttributeValue": "10000"},
                    ],
                    "ProductCompliance": [
                        {"ComplianceName": "REACH", "ComplianceValue": "Compliant"},
                        {"ComplianceName": "Conflict Minerals", "ComplianceValue": "Compliant"},
                        # Should NOT shadow the existing RoHS row from ROHSStatus
                        {"ComplianceName": "RoHS", "ComplianceValue": "Different value"},
                    ],
                    "REACH-SVHC": ["Lead", "Cadmium"],
                    "IsDiscontinued": "False",
                    "SuggestedReplacement": "RC0402JR-070R-NEW",
                    "RestrictionMessage": "",
                    "AvailabilityInStock": "5000",
                    "AvailableOnOrder": "0",
                    "SalesMaximumOrderQty": "0",
                    "AlternatePackagings": [
                        {"APMfrPN": "RC0402JR-070RP"},
                        {"APMfrPN": "RC0402JR-070RT"},
                    ],
                    "UnitWeightKg": {"UnitWeight": 0.0001},
                    "IPCCode": "RES_0402_2P_5%",
                }
            ],
        },
    }


def test_provider_emits_packaging_plus_description_plus_direct(monkeypatch):
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_full_response(),
    )
    out = MouserProvider("k").lookup_mpn("RC0402JR-070R")
    assert out["found"]
    keys = [s["key"] for s in out["result"]["specs"]]
    # From ProductAttributes
    assert "Packaging" in keys
    assert "Standard Pack Qty" in keys
    # From description
    assert "Resistance" in keys
    assert "Tolerance" in keys
    assert "Power" in keys
    assert "Package" in keys
    # Direct fields
    assert "Lifecycle" in keys
    assert "RoHS" in keys
    assert "Availability" in keys
    assert "Lead time" in keys
    # MOQ=1 / Mult=1 are skipped — they'd just be noise.
    assert "MOQ" not in keys
    assert "Order multiple" not in keys
    # AvailableOnOrder=0 / SalesMaximumOrderQty=0 likewise skipped.
    assert "On order (qty)" not in keys
    assert "Max order qty" not in keys
    # Empty RestrictionMessage / IsDiscontinued=False likewise skipped.
    assert "Restriction" not in keys
    assert "Discontinued" not in keys

    by_key = {s["key"]: s["value"] for s in out["result"]["specs"]}
    assert "Reel" in by_key["Packaging"] and "Cut Tape" in by_key["Packaging"]
    assert out["result"]["footprint"] == "0402"


def test_provider_emits_full_price_ladder(monkeypatch):
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_full_response(),
    )
    out = MouserProvider("k").lookup_mpn("X")
    by_key = {s["key"]: s["value"] for s in out["result"]["specs"]}
    assert by_key["Unit price (1+)"] == "$0.10"
    assert by_key["Unit price (100+)"] == "$0.05"
    assert by_key["Unit price (1000+)"] == "$0.02"


def test_provider_surfaces_extra_identity_fields(monkeypatch):
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_full_response(),
    )
    out = MouserProvider("k").lookup_mpn("X")
    by_key = {s["key"]: s["value"] for s in out["result"]["specs"]}
    assert by_key["Mouser P/N"] == "603-RC0402JR-070R"
    # ActualMfrName differs from Manufacturer ("YAGEO") so it surfaces.
    assert by_key["Distributor mfr name"] == "YAGEO Phycomp"


def test_provider_emits_compliance_without_shadowing_rohs(monkeypatch):
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_full_response(),
    )
    out = MouserProvider("k").lookup_mpn("X")
    by_key = {s["key"]: s["value"] for s in out["result"]["specs"]}
    # ProductCompliance entries land as their own rows...
    assert by_key["REACH"] == "Compliant"
    assert by_key["Conflict Minerals"] == "Compliant"
    # ...except for RoHS, which is already emitted from ROHSStatus and
    # must NOT be replaced by the ProductCompliance entry.
    assert by_key["RoHS"] == "RoHS Compliant"
    # REACH-SVHC list joined with ", "
    assert by_key["REACH SVHC"] == "Lead, Cadmium"


def test_provider_emits_alt_packagings_unit_weight_ipc(monkeypatch):
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_full_response(),
    )
    out = MouserProvider("k").lookup_mpn("X")
    by_key = {s["key"]: s["value"] for s in out["result"]["specs"]}
    assert by_key["Alternate packagings"] == "RC0402JR-070RP / RC0402JR-070RT"
    assert by_key["Unit weight"] == "0.0001 kg"
    assert by_key["IPC code"] == "RES_0402_2P_5%"
    assert by_key["In stock (qty)"] == "5000"


def test_provider_emits_discontinued_and_restriction_when_set(monkeypatch):
    resp = _stub_full_response()
    resp["SearchResults"]["Parts"][0]["IsDiscontinued"] = "True"
    resp["SearchResults"]["Parts"][0]["RestrictionMessage"] = "Export controlled"
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: resp,
    )
    out = MouserProvider("k").lookup_mpn("X")
    by_key = {s["key"]: s["value"] for s in out["result"]["specs"]}
    assert by_key["Discontinued"] == "yes"
    assert by_key["Restriction"] == "Export controlled"
    # Suggested replacement still surfaces.
    assert by_key["Suggested replacement"] == "RC0402JR-070R-NEW"


def test_provider_skips_actual_mfr_when_same_as_manufacturer(monkeypatch):
    resp = _stub_full_response()
    resp["SearchResults"]["Parts"][0]["ActualMfrName"] = "YAGEO"
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: resp,
    )
    out = MouserProvider("k").lookup_mpn("X")
    keys = [s["key"] for s in out["result"]["specs"]]
    assert "Distributor mfr name" not in keys


def test_provider_emits_moq_and_mult_when_above_one(monkeypatch):
    resp = _stub_full_response()
    resp["SearchResults"]["Parts"][0]["Min"] = "100"
    resp["SearchResults"]["Parts"][0]["Mult"] = "10"
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: resp,
    )
    out = MouserProvider("k").lookup_mpn("X")
    by_key = {s["key"]: s["value"] for s in out["result"]["specs"]}
    assert by_key["MOQ"] == "100"
    assert by_key["Order multiple"] == "10"
