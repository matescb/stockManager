"""A3 — normalising provider specs on import and refresh.

`domain/parts/services/spec_reconcile.py` is the single place a provider
payload becomes `custom_fields` rows, for BOTH the create path
(`services/provider_import.py`) and the refresh route
(`api/routes/parts_refresh.py`).

Four properties are pinned here:

* **canonical keys are shared, and precedence decides them.** Both tiers
  now write the same un-namespaced `resistance` row; who wins is
  `custom_fields.provider` plus `spec_schema.PROVIDER_PRECEDENCE`, not
  who refreshed last (ADR-0034).
* **catalog and optional keys did not move.** They keep the exact keys
  and namespace rules ADR-0031 gave them, because `providerCatalog.ts`
  and the Sourcing tab key off those names.
* **junk never lands.** Customs codes and `-` values are archived, not
  stored, and never rewritten.
* **nothing a user touched is overwritten.** `manual` and `override`
  rows survive every refresh from every provider.

The stubs come from `test_secondary_provider.py` rather than being
re-derived — one definition of what a DigiKey/Mouser payload looks like.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests._factories import signup_user
from tests.test_secondary_provider import (
    _configure_mouser_secondary,
    _enable_digikey_primary,
    _stub_digikey,
    _stub_mouser,
)

MPN = "RC0402FR-0710KL"


# ---------------------------------------------------------------------------
# Payloads — a real-shaped thick-film chip resistor from each provider.
# ---------------------------------------------------------------------------

DIGIKEY_RESISTOR = {
    "ManufacturerProductNumber": MPN,
    "Manufacturer": {"Name": "YAGEO"},
    "Description": {"ProductDescription": "RES SMD 10K OHM 1% 1/16W 0402"},
    "Category": {"Name": "Chip Resistor - Surface Mount"},
    "ProductUrl": "https://www.digikey.com/p/1",
    "Parameters": [
        {"ParameterText": "Resistance", "ValueText": "10 kOhms"},
        {"ParameterText": "Tolerance", "ValueText": "±1%"},
        {"ParameterText": "Power (Watts)", "ValueText": "0.063W, 1/16W"},
        {"ParameterText": "Temperature Coefficient", "ValueText": "±100ppm/°C"},
        {"ParameterText": "Package / Case", "ValueText": "0402 (1005 Metric)"},
        {"ParameterText": "Mounting Type", "ValueText": "Surface Mount"},
        # Junk — compliance codes that are not specs.
        {"ParameterText": "ECCN", "ValueText": "EAR99"},
        {"ParameterText": "HTS code", "ValueText": "8533.21.0030"},
        {"ParameterText": "MSL", "ValueText": "1 (Unlimited)"},
        # A placeholder value: ~1,000 prod rows look exactly like this.
        {"ParameterText": "Failure Rate", "ValueText": "-"},
        # Catalog metadata — Sourcing tab, not Specs.
        {"ParameterText": "Packaging", "ValueText": "Tape & Reel (TR)"},
        {"ParameterText": "Series", "ValueText": "RC"},
        # Optional parametric — kept verbatim so ICs lose nothing.
        {"ParameterText": "Features", "ValueText": "Moisture Resistant"},
    ],
}

MOUSER_RESISTOR = {
    "Manufacturer": "YAGEO",
    "ManufacturerPartNumber": MPN,
    "Description": "Thick Film Resistors - SMD 0402 10 kOhms 5% 1/16W",
    "Category": "Thick Film Resistors - SMD",
    "ProductDetailUrl": "https://www.mouser.com/p/1",
    "ProductAttributes": [
        {"AttributeName": "Resistance", "AttributeValue": "10 kOhms"},
        {"AttributeName": "Tolerance", "AttributeValue": "5 %"},
        {"AttributeName": "Packaging", "AttributeValue": "Cut Tape"},
        {"AttributeName": "TARIC", "AttributeValue": "8533210000"},
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ws(c: TestClient, email: str | None = None) -> uuid.UUID:
    return uuid.UUID(signup_user(c, email=email).json()["data"]["workspace_id"])


@pytest.fixture
def authed(db) -> TestClient:
    c = TestClient(app)
    _ws(c)
    return c


def _category(c: TestClient, name: str, parent_id: str | None = None) -> str:
    body: dict = {"name": name}
    if parent_id:
        body["parent_id"] = parent_id
    r = c.post("/api/categories", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _part(c: TestClient, mpn: str = MPN, **kwargs) -> str:
    r = c.post("/api/parts", json={"name": mpn, "part_type": "linked", "mpn": mpn, **kwargs})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _fields(c: TestClient, part_id: str) -> dict[str, dict]:
    r = c.get(f"/api/custom-fields/by-object/part/{part_id}")
    assert r.status_code == 200, r.text
    return {row["key"]: row for row in r.json()["data"]}


def _refresh(c: TestClient, part_id: str, provider: str | None = None) -> dict:
    query = f"?provider={provider}" if provider else ""
    r = c.post(f"/api/parts/{part_id}/refresh-from-provider{query}")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _detail(c: TestClient, part_id: str) -> dict:
    r = c.get(f"/api/parts/{part_id}")
    assert r.status_code == 200, r.text
    return r.json()["data"]


# ---------------------------------------------------------------------------
# Canonical keys from a DigiKey payload
# ---------------------------------------------------------------------------


def test_a_digikey_payload_becomes_canonical_parsed_rows(authed, monkeypatch):
    _enable_digikey_primary(authed)
    _category(authed, "Resistors")
    part_id = _part(authed)
    _stub_digikey(monkeypatch, DIGIKEY_RESISTOR)

    _refresh(authed, part_id)
    rows = _fields(authed, part_id)

    assert rows["resistance"]["value"] == "10 kΩ"
    assert rows["resistance"]["value_num"] == "10000"
    assert rows["resistance"]["provider"] == "digikey"
    assert rows["resistance"]["source"] == "provider"
    assert rows["tolerance"]["value"] == "1%"
    assert rows["power"]["value"] == "63 mW"
    assert rows["power"]["value_num"] == "0.063"
    assert rows["temp_coefficient"]["value"] == "100 ppm/°C"
    assert rows["mounting"]["value"] == "Surface Mount"
    # `package` has no unit, so it is kept verbatim and carries no number.
    assert rows["package"]["value"] == "0402 (1005 Metric)"
    assert rows["package"]["value_num"] is None
    # ...and the raw vendor spellings are gone.
    assert "Resistance" not in rows
    assert "Package / Case" not in rows


def test_junk_keys_and_placeholder_values_are_never_written(authed, monkeypatch):
    _enable_digikey_primary(authed)
    part_id = _part(authed)
    _stub_digikey(monkeypatch, DIGIKEY_RESISTOR)

    _refresh(authed, part_id)
    rows = _fields(authed, part_id)

    for junk in ("ECCN", "HTS code", "MSL", "Failure Rate"):
        assert junk not in rows, f"{junk} reached the Specs tab"


def test_catalog_and_optional_keys_keep_their_exact_names(authed, monkeypatch):
    """`providerCatalog.ts` and the Sourcing tab key off these strings —
    renaming them would move the rows to the wrong tab."""
    _enable_digikey_primary(authed)
    part_id = _part(authed)
    _stub_digikey(monkeypatch, DIGIKEY_RESISTOR)

    _refresh(authed, part_id)
    rows = _fields(authed, part_id)

    assert rows["Packaging"]["value"] == "Tape & Reel (TR)"
    assert rows["Series"]["value"] == "RC"
    assert rows["Features"]["value"] == "Moisture Resistant"
    assert rows["source_url"]["value"] == "https://www.digikey.com/p/1"


def test_a_secondary_writes_canonical_keys_un_namespaced(authed, monkeypatch):
    """The point of A3: "load from both providers" is meaningless while a
    secondary's parametric data sits under a prefix nothing reads."""
    _enable_digikey_primary(authed)
    _configure_mouser_secondary(authed)
    part_id = _part(authed)
    _stub_mouser(monkeypatch, MOUSER_RESISTOR)

    _refresh(authed, part_id, "mouser")
    rows = _fields(authed, part_id)

    assert rows["resistance"]["value"] == "10 kΩ"
    assert rows["resistance"]["provider"] == "mouser"
    assert "mouser:resistance" not in rows
    # ...while its catalog rows stay exactly where ADR-0031 put them.
    assert rows["mouser:Packaging"]["value"] == "Cut Tape"
    assert rows["mouser:source_url"]["value"] == "https://www.mouser.com/p/1"
    assert "Packaging" not in rows
    # Junk is junk in either namespace.
    assert "mouser:TARIC" not in rows


# ---------------------------------------------------------------------------
# Precedence — DigiKey > Mouser, and never the other way round
# ---------------------------------------------------------------------------


def test_digikey_overwrites_a_mouser_canonical_value(authed, monkeypatch):
    _enable_digikey_primary(authed)
    _configure_mouser_secondary(authed)
    part_id = _part(authed)

    _stub_mouser(monkeypatch, MOUSER_RESISTOR)
    _refresh(authed, part_id, "mouser")
    assert _fields(authed, part_id)["tolerance"]["value"] == "5%"

    _stub_digikey(monkeypatch, DIGIKEY_RESISTOR)
    _refresh(authed, part_id)

    rows = _fields(authed, part_id)
    assert rows["tolerance"]["value"] == "1%"
    assert rows["tolerance"]["provider"] == "digikey"


def test_mouser_never_overwrites_a_digikey_canonical_value(authed, monkeypatch):
    """DigiKey's `Parameters[]` is a real attribute table; Mouser's value
    for the same key is mined out of prose. Refresh order must not
    decide which one the part keeps."""
    _enable_digikey_primary(authed)
    _configure_mouser_secondary(authed)
    part_id = _part(authed)

    _stub_digikey(monkeypatch, DIGIKEY_RESISTOR)
    _refresh(authed, part_id)
    _stub_mouser(monkeypatch, MOUSER_RESISTOR)
    _refresh(authed, part_id, "mouser")

    rows = _fields(authed, part_id)
    assert rows["tolerance"]["value"] == "1%"
    assert rows["tolerance"]["provider"] == "digikey"


def test_mouser_fills_a_canonical_key_digikey_did_not_answer(authed, monkeypatch):
    """Losing on precedence is per KEY, not per payload — a secondary
    still contributes everything the primary left empty."""
    _enable_digikey_primary(authed)
    _configure_mouser_secondary(authed)
    part_id = _part(authed)

    thin = dict(DIGIKEY_RESISTOR)
    thin["Parameters"] = [{"ParameterText": "Resistance", "ValueText": "10 kOhms"}]
    _stub_digikey(monkeypatch, thin)
    _refresh(authed, part_id)
    assert "tolerance" not in _fields(authed, part_id)

    _stub_mouser(monkeypatch, MOUSER_RESISTOR)
    _refresh(authed, part_id, "mouser")

    rows = _fields(authed, part_id)
    assert rows["tolerance"]["value"] == "5%"
    assert rows["tolerance"]["provider"] == "mouser"
    assert rows["resistance"]["provider"] == "digikey"


# ---------------------------------------------------------------------------
# The delete pass — ADR-0031's non-interference, now across shared keys
# ---------------------------------------------------------------------------


def test_a_primary_refresh_keeps_a_canonical_row_the_secondary_owns(
    authed, monkeypatch
):
    """The delete pass drops every `source='provider'` row absent from
    the payload. Canonical keys are un-namespaced, so the prefix rule no
    longer bounds it — `custom_fields.provider` does."""
    _enable_digikey_primary(authed)
    _configure_mouser_secondary(authed)
    part_id = _part(authed)

    _stub_mouser(monkeypatch, MOUSER_RESISTOR)
    _refresh(authed, part_id, "mouser")

    thin = dict(DIGIKEY_RESISTOR)
    thin["Parameters"] = [{"ParameterText": "Package / Case", "ValueText": "0402"}]
    _stub_digikey(monkeypatch, thin)
    _refresh(authed, part_id)

    rows = _fields(authed, part_id)
    assert rows["resistance"]["value"] == "10 kΩ"
    assert rows["resistance"]["provider"] == "mouser"
    assert rows["package"]["provider"] == "digikey"


def test_a_secondary_refresh_keeps_a_canonical_row_the_primary_owns(
    authed, monkeypatch
):
    _enable_digikey_primary(authed)
    _configure_mouser_secondary(authed)
    part_id = _part(authed)

    _stub_digikey(monkeypatch, DIGIKEY_RESISTOR)
    _refresh(authed, part_id)

    thin = dict(MOUSER_RESISTOR)
    thin["Description"] = "Thick Film Resistors - SMD"
    thin["ProductAttributes"] = [
        {"AttributeName": "Packaging", "AttributeValue": "Cut Tape"}
    ]
    _stub_mouser(monkeypatch, thin)
    _refresh(authed, part_id, "mouser")

    rows = _fields(authed, part_id)
    assert rows["resistance"]["value"] == "10 kΩ"
    assert rows["power"]["value"] == "63 mW"


def test_a_provider_still_prunes_its_own_stale_canonical_row(authed, monkeypatch):
    _enable_digikey_primary(authed)
    part_id = _part(authed)

    _stub_digikey(monkeypatch, DIGIKEY_RESISTOR)
    _refresh(authed, part_id)
    assert "tolerance" in _fields(authed, part_id)

    thin = dict(DIGIKEY_RESISTOR)
    thin["Parameters"] = [{"ParameterText": "Resistance", "ValueText": "10 kOhms"}]
    _stub_digikey(monkeypatch, thin)
    _refresh(authed, part_id)

    assert "tolerance" not in _fields(authed, part_id)


def test_the_first_refresh_retires_the_old_raw_rows(authed, db, monkeypatch):
    """Every one of the 9,377 prod provider rows is an un-normalised
    `source='provider'` row with a NULL `provider`. The primary owns
    un-namespaced keys, so its next refresh replaces `Resistance` with
    `resistance` rather than leaving the Specs tab showing both."""
    from app.domain.custom_fields.models import CustomField

    _enable_digikey_primary(authed)
    part_id = _part(authed)
    ws_id = uuid.UUID(authed.get("/api/workspaces/current").json()["data"]["id"])
    db.add(
        CustomField(
            workspace_id=ws_id,
            object_type="part",
            object_id=uuid.UUID(part_id),
            key="Resistance",
            value="10k",
            source="provider",
        )
    )
    db.add(
        CustomField(
            workspace_id=ws_id,
            object_type="part",
            object_id=uuid.UUID(part_id),
            key="ECCN",
            value="EAR99",
            source="provider",
        )
    )
    db.flush()

    _stub_digikey(monkeypatch, DIGIKEY_RESISTOR)
    _refresh(authed, part_id)

    rows = _fields(authed, part_id)
    assert rows["resistance"]["value"] == "10 kΩ"
    assert "Resistance" not in rows
    assert "ECCN" not in rows


# ---------------------------------------------------------------------------
# Manual and override rows
# ---------------------------------------------------------------------------


def test_a_manual_row_on_a_canonical_key_is_never_overwritten(authed, monkeypatch):
    _enable_digikey_primary(authed)
    part_id = _part(authed)
    r = authed.post(
        "/api/custom-fields",
        json={
            "object_type": "part",
            "object_id": part_id,
            "key": "resistance",
            "value": "measured 9.98 kΩ",
        },
    )
    assert r.status_code == 201, r.text

    _stub_digikey(monkeypatch, DIGIKEY_RESISTOR)
    _refresh(authed, part_id)

    row = _fields(authed, part_id)["resistance"]
    assert row["value"] == "measured 9.98 kΩ"
    assert row["source"] == "manual"


def test_an_override_keeps_its_value_and_tracks_the_new_upstream(authed, monkeypatch):
    """Editing a provider row makes it an `override`; a later refresh
    must not undo the edit, but Restore has to land on what upstream says
    NOW — the behaviour `_reconcile_provider_fields` already had."""
    _enable_digikey_primary(authed)
    part_id = _part(authed)
    _stub_digikey(monkeypatch, DIGIKEY_RESISTOR)
    _refresh(authed, part_id)

    r = authed.post(
        "/api/custom-fields",
        json={
            "object_type": "part",
            "object_id": part_id,
            "key": "tolerance",
            "value": "0.5%",
        },
    )
    assert r.status_code == 201, r.text
    assert _fields(authed, part_id)["tolerance"]["source"] == "override"

    moved = dict(DIGIKEY_RESISTOR)
    moved["Parameters"] = [
        dict(p, ValueText="±2%") if p["ParameterText"] == "Tolerance" else p
        for p in DIGIKEY_RESISTOR["Parameters"]
    ]
    _stub_digikey(monkeypatch, moved)
    _refresh(authed, part_id)

    row = _fields(authed, part_id)["tolerance"]
    assert row["value"] == "0.5%"
    assert row["source"] == "override"
    assert row["original_value"] == "2%"


# ---------------------------------------------------------------------------
# Category assignment (A4, wired)
# ---------------------------------------------------------------------------


def test_refresh_files_an_uncategorized_part(authed, monkeypatch):
    _enable_digikey_primary(authed)
    category_id = _category(authed, "Resistors")
    part_id = _part(authed)
    _stub_digikey(monkeypatch, DIGIKEY_RESISTOR)

    body = _refresh(authed, part_id)
    assert body["part"]["category_id"] == category_id
    assert _detail(authed, part_id)["category_id"] == category_id


def test_refresh_never_overrides_a_category_the_user_chose(authed, monkeypatch):
    _enable_digikey_primary(authed)
    _category(authed, "Resistors")
    mine = _category(authed, "Feedback dividers")
    part_id = _part(authed, category_id=mine)
    _stub_digikey(monkeypatch, DIGIKEY_RESISTOR)

    body = _refresh(authed, part_id)
    assert body["part"]["category_id"] == mine


def test_an_unresolvable_category_comes_back_as_a_suggestion(authed, monkeypatch):
    _enable_digikey_primary(authed)
    part_id = _part(authed)
    _stub_digikey(monkeypatch, DIGIKEY_RESISTOR)

    body = _refresh(authed, part_id)
    assert body["part"]["category_id"] is None
    assert body["category_suggestion"] == "Resistors"


def test_a_leaf_that_does_not_exist_files_under_the_root(authed, monkeypatch):
    _enable_digikey_primary(authed)
    root = _category(authed, "Capacitors")
    part_id = _part(authed, mpn="CL05B104KO5NNNC")
    ceramic = dict(DIGIKEY_RESISTOR)
    ceramic["ManufacturerProductNumber"] = "CL05B104KO5NNNC"
    ceramic["Category"] = {"Name": "Ceramic Capacitors"}
    ceramic["Parameters"] = [
        {"ParameterText": "Capacitance", "ValueText": "0.1µF"},
        {"ParameterText": "Voltage - Rated", "ValueText": "50V"},
        {"ParameterText": "Temperature Coefficient", "ValueText": "X7R"},
        {"ParameterText": "Package / Case", "ValueText": "0402 (1005 Metric)"},
    ]
    _stub_digikey(monkeypatch, ceramic)

    body = _refresh(authed, part_id)
    assert body["part"]["category_id"] == root
    # ...and it still gets the CERAMIC schema. The part is filed under
    # Capacitors because that is as deep as this tree goes, but it is a
    # ceramic capacitor, and only the ceramic schema reads `X7R` as a
    # dielectric. A bare "Capacitors" classifies to nothing on its own.
    assert _fields(authed, part_id)["dielectric"]["value"] == "X7R"


def test_the_category_picks_the_spec_schema(authed, monkeypatch):
    """A ceramic capacitor files `X7R` under `dielectric`; a resistor
    files the same DigiKey `Temperature Coefficient` name under
    `temp_coefficient`. The category is what tells them apart."""
    _enable_digikey_primary(authed)
    root = _category(authed, "Capacitors")
    _category(authed, "Ceramic", parent_id=root)
    part_id = _part(authed, mpn="CL05B104KO5NNNC")
    ceramic = dict(DIGIKEY_RESISTOR)
    ceramic["ManufacturerProductNumber"] = "CL05B104KO5NNNC"
    ceramic["Category"] = {"Name": "Ceramic Capacitors"}
    ceramic["Parameters"] = [
        {"ParameterText": "Capacitance", "ValueText": "0.1µF"},
        {"ParameterText": "Voltage - Rated", "ValueText": "50V"},
        {"ParameterText": "Temperature Coefficient", "ValueText": "X7R"},
        {"ParameterText": "Package / Case", "ValueText": "0402 (1005 Metric)"},
    ]
    _stub_digikey(monkeypatch, ceramic)

    _refresh(authed, part_id)
    rows = _fields(authed, part_id)
    assert rows["capacitance"]["value"] == "100 nF"
    assert rows["voltage_rating"]["value"] == "50 V"
    assert rows["dielectric"]["value"] == "X7R"
    assert "temp_coefficient" not in rows


# ---------------------------------------------------------------------------
# spec_incomplete / missing_specs
# ---------------------------------------------------------------------------


def test_detail_reports_the_mandatory_keys_nobody_supplied(authed, monkeypatch):
    _enable_digikey_primary(authed)
    _category(authed, "Resistors")
    part_id = _part(authed)
    thin = dict(DIGIKEY_RESISTOR)
    thin["Parameters"] = [
        {"ParameterText": "Resistance", "ValueText": "10 kOhms"},
        {"ParameterText": "Package / Case", "ValueText": "0402"},
    ]
    _stub_digikey(monkeypatch, thin)
    _refresh(authed, part_id)

    body = _detail(authed, part_id)
    assert body["spec_incomplete"] is True
    assert body["missing_specs"] == ["tolerance", "power", "temp_coefficient"]


def test_a_fully_specified_part_is_not_incomplete(authed, monkeypatch):
    _enable_digikey_primary(authed)
    _category(authed, "Resistors")
    part_id = _part(authed)
    _stub_digikey(monkeypatch, DIGIKEY_RESISTOR)
    _refresh(authed, part_id)

    body = _detail(authed, part_id)
    assert body["missing_specs"] == []
    assert body["spec_incomplete"] is False


def test_a_manual_row_counts_as_supplied(authed):
    """The flag answers "does this part have the data", not "did a
    provider send it" — a value the user typed is still the value."""
    category_id = _category(authed, "Resistors")
    part_id = _part(authed, mpn="MANUAL-1", category_id=category_id)
    for key, value in (
        ("package", "0402"),
        ("resistance", "10 kΩ"),
        ("tolerance", "1%"),
        ("power", "63 mW"),
        ("temp_coefficient", "100 ppm/°C"),
    ):
        r = authed.post(
            "/api/custom-fields",
            json={"object_type": "part", "object_id": part_id, "key": key, "value": value},
        )
        assert r.status_code == 201, r.text

    assert _detail(authed, part_id)["missing_specs"] == []


def test_the_list_row_carries_the_same_flag(authed, monkeypatch):
    _enable_digikey_primary(authed)
    _category(authed, "Resistors")
    part_id = _part(authed)
    _stub_digikey(monkeypatch, DIGIKEY_RESISTOR)
    _refresh(authed, part_id)

    rows = authed.get("/api/parts?paged=true").json()["data"]["items"]
    row = next(r for r in rows if r["id"] == part_id)
    assert row["spec_incomplete"] is False
    assert row["missing_specs"] == []


def test_the_list_flag_does_not_scale_with_row_count(authed, db, engine):
    """One batched query for the page, like every other list extra —
    `serialize_part_rows` is where a per-row lookup would hide."""
    from sqlalchemy import event

    category_id = _category(authed, "Resistors")

    def add_parts(prefix: str, count: int) -> None:
        for index in range(count):
            _part(authed, mpn=f"{prefix}-{index}", category_id=category_id)

    def query_count(expect: int) -> int:
        count = 0

        def _on_execute(conn, cursor, statement, parameters, context, executemany):
            nonlocal count
            count += 1

        event.listen(engine, "before_cursor_execute", _on_execute)
        try:
            rows = authed.get("/api/parts?paged=true&limit=200").json()["data"]["items"]
        finally:
            event.remove(engine, "before_cursor_execute", _on_execute)
        assert len(rows) == expect
        assert count > 0, "the counter saw nothing — it is on the wrong engine"
        return count

    add_parts("SI-small", 2)
    small = query_count(2)
    add_parts("SI-large", 18)
    large = query_count(20)
    assert large <= small + 1, (
        f"listing issued {large} queries for 20 parts vs {small} for 2 — "
        "the spec-completeness lookup has regressed into an N+1"
    )


# ---------------------------------------------------------------------------
# Create path — bulk import from a scan
# ---------------------------------------------------------------------------


def test_bulk_import_normalises_and_files_the_new_part(authed, monkeypatch):
    _enable_digikey_primary(authed)
    category_id = _category(authed, "Resistors")
    _stub_digikey(monkeypatch, DIGIKEY_RESISTOR)

    r = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{"mpn": MPN, "quantity": 0}], "idempotency_key": uuid.uuid4().hex},
    )
    assert r.status_code == 200, r.text
    row = r.json()["data"]["rows"][0]
    assert row["status"] == "created", row

    rows = _fields(authed, row["part_id"])
    assert rows["resistance"]["value"] == "10 kΩ"
    assert rows["resistance"]["provider"] == "digikey"
    assert "ECCN" not in rows
    assert _detail(authed, row["part_id"])["category_id"] == category_id


def test_bulk_import_reports_a_category_it_could_not_resolve(authed, monkeypatch):
    _enable_digikey_primary(authed)
    _stub_digikey(monkeypatch, DIGIKEY_RESISTOR)

    r = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{"mpn": MPN, "quantity": 0}], "idempotency_key": uuid.uuid4().hex},
    )
    assert r.status_code == 200, r.text
    row = r.json()["data"]["rows"][0]
    assert row["category_suggestion"] == "Resistors"


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def test_a_reconcile_writes_one_audit_row_naming_keys_not_values(authed, monkeypatch, db):
    from sqlalchemy import select

    from app.domain.audit.models import AuditLog

    _enable_digikey_primary(authed)
    part_id = _part(authed)
    _stub_digikey(monkeypatch, DIGIKEY_RESISTOR)
    _refresh(authed, part_id)

    rows = list(
        db.execute(
            select(AuditLog).where(AuditLog.action == "part.specs_reconciled")
        ).scalars()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.target_type == "part"
    assert row.target_ids == [uuid.UUID(part_id)]
    assert row.workspace_id is not None
    assert "provider=digikey" in row.comment
    assert "resistance" in row.comment
    # Key names only — never a value, which is upstream text we do not
    # control and the one thing an audit comment must not carry.
    assert "10 kΩ" not in row.comment
    assert "Tape & Reel" not in row.comment


# ---------------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------------


def test_a_refresh_files_into_its_own_workspaces_category(db, monkeypatch):
    """`resolve_category_path` matches by NAME, so two workspaces with a
    "Resistors" category must not be able to reach each other's row."""
    other = TestClient(app)
    _ws(other, "spec-iso-other@example.com")
    other_resistors = _category(other, "Resistors")

    mine = TestClient(app)
    _ws(mine, "spec-iso-mine@example.com")
    _enable_digikey_primary(mine)
    my_resistors = _category(mine, "Resistors")
    part_id = _part(mine)
    _stub_digikey(monkeypatch, DIGIKEY_RESISTOR)

    body = _refresh(mine, part_id)
    assert body["part"]["category_id"] == my_resistors
    assert body["part"]["category_id"] != other_resistors


# ---------------------------------------------------------------------------
# Archived rows and `uq_cf_unique`
# ---------------------------------------------------------------------------


def test_a_key_upstream_answers_again_is_restored_not_re_inserted(
    authed, db, monkeypatch
):
    """`uq_cf_unique` has no partial WHERE, so an archived row still owns
    its key. Prod has ~1,000 rows whose value is `-`; the first refresh
    that omits one archives it, and the next refresh that carries a real
    value for the same key would collide on insert — a 500 on refresh, and
    a rolled-back row in the middle of a bulk import.
    """
    from app.domain.custom_fields.models import CustomField

    _enable_digikey_primary(authed)
    part_id = _part(authed)
    ws_id = uuid.UUID(authed.get("/api/workspaces/current").json()["data"]["id"])
    db.add(
        CustomField(
            workspace_id=ws_id,
            object_type="part",
            object_id=uuid.UUID(part_id),
            key="Packaging",
            value="-",
            source="provider",
        )
    )
    db.flush()

    without = dict(DIGIKEY_RESISTOR)
    without["Parameters"] = [
        p for p in DIGIKEY_RESISTOR["Parameters"] if p["ParameterText"] != "Packaging"
    ]
    _stub_digikey(monkeypatch, without)
    body = _refresh(authed, part_id)
    assert body["summary"]["archived"] >= 1
    assert "Packaging" not in _fields(authed, part_id)

    _stub_digikey(monkeypatch, DIGIKEY_RESISTOR)
    _refresh(authed, part_id)

    rows = _fields(authed, part_id)
    assert rows["Packaging"]["value"] == "Tape & Reel (TR)"
    assert (
        db.query(CustomField)
        .filter_by(object_id=uuid.UUID(part_id), key="Packaging")
        .count()
        == 1
    )


def test_a_manual_edit_brings_an_archived_row_back(authed, db, monkeypatch):
    """Same constraint from the other side: typing a retired key into the
    add-spec form must restore the row, not update one the list endpoint
    will never show again."""
    from app.domain.custom_fields.models import CustomField

    _enable_digikey_primary(authed)
    part_id = _part(authed)
    _stub_digikey(monkeypatch, DIGIKEY_RESISTOR)
    _refresh(authed, part_id)
    assert "ECCN" not in _fields(authed, part_id)

    r = authed.post(
        "/api/custom-fields",
        json={"object_type": "part", "object_id": part_id, "key": "ECCN", "value": "mine"},
    )
    assert r.status_code in (200, 201), r.text

    rows = _fields(authed, part_id)
    assert rows["ECCN"]["value"] == "mine"
    assert (
        db.query(CustomField)
        .filter_by(object_id=uuid.UUID(part_id), key="ECCN")
        .count()
        == 1
    )


def test_a_bare_upstream_key_that_spells_a_canonical_one_is_not_a_second_row(
    authed, monkeypatch
):
    """`uq_cf_unique` allows one row per key, so an upstream parameter
    literally named `package` cannot coexist with the canonical `package`
    the same payload produced. The canonical row is the better answer;
    without this the insert is a 500 on vendor data we do not control."""
    _enable_digikey_primary(authed)
    _category(authed, "Resistors")
    part_id = _part(authed)
    odd = dict(DIGIKEY_RESISTOR)
    odd["Parameters"] = [
        {"ParameterText": "Package / Case", "ValueText": "0402 (1005 Metric)"},
        {"ParameterText": "package", "ValueText": "whatever the vendor meant"},
    ]
    _stub_digikey(monkeypatch, odd)

    _refresh(authed, part_id)

    rows = _fields(authed, part_id)
    assert rows["package"]["value"] == "0402 (1005 Metric)"
