"""SM-004 — lifecycle and compliance as structured fields.

Three layers, each against what the code really sees:

* the status vocabulary (`normalise_status`) over the strings DigiKey's
  `ProductStatus.Status` and Mouser's `LifecycleStatus` use;
* recorded-shape DigiKey and Mouser responses through the real adapters
  (`_record_from_product`, `MouserProvider.lookup_mpn`) into
  `from_record` — what `catalog_lookup` returns;
* `get_part` / `export_parts` over rows written by the REAL refresh path
  (`spec_reconcile`, both tiers), so "values come from the same
  provider-owned rows" is tested rather than assumed: precedence and
  `alternates`, a manual value, unknowns, and workspace isolation.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.domain.custom_fields.models import CustomField
from app.domain.parts import lifecycle_compliance as lc
from app.domain.parts.models import PartProviderLink
from app.domain.parts.providers.digikey import _record_from_product
from app.domain.parts.providers.mouser import MouserProvider
from app.domain.parts.spec_schema import PROVIDER_PRECEDENCE
from app.main import app
from tests._factories import signup_user
from tests.test_digikey_provider import _stub_product
from tests.test_mcp import _mint, call, mcp_session
from tests.test_mouser_extraction import _stub_full_response
from tests.test_secondary_provider import (
    _configure_mouser_secondary,
    _create_part,
    _enable_digikey_primary,
    _stub_digikey,
    _stub_mouser,
)

# The real disagreement that motivated `alternates`: Mouser lists the part
# obsolete while DigiKey still sells it as active.
MPN = "SUD50P06-15L-E3"


def _specs(record: dict) -> dict[str, str]:
    return {row["key"]: row["value"] for row in record["specs"]}


# ---------------------------------------------------------------------------
# vocabulary
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("raw", "status"), [
    # DigiKey ProductStatus.Status
    ("Active", "active"),
    ("Not For New Designs", "nrnd"),
    ("Last Time Buy", "last-time-buy"),
    ("Obsolete", "obsolete"),
    ("Discontinued at Digi-Key", "discontinued"),
    ("Preliminary", "preliminary"),
    # Mouser LifecycleStatus
    ("Production", "active"),
    ("New Product", "active"),
    ("New at Mouser", "active"),
    ("Not Recommended for New Designs", "nrnd"),
    ("NRND", "nrnd"),
    ("End of Life", "last-time-buy"),
    ("Obsolete", "obsolete"),
    # Unmapped or absent
    ("Factory Special Order", "unknown"),
    ("Inactive", "unknown"),
    ("", "unknown"),
    (None, "unknown"),
])
def test_status_vocabulary(raw, status):
    assert lc.normalise_status(raw) == status
    assert status in lc.LIFECYCLE_STATUSES


def test_provider_rank_matches_the_spec_schema_precedence():
    assert lc.PROVIDER_RANK == PROVIDER_PRECEDENCE


# ---------------------------------------------------------------------------
# provider records (catalog_lookup)
# ---------------------------------------------------------------------------
def test_recorded_digikey_product_maps_to_structured_fields():
    record = _record_from_product(_stub_product())
    out = lc.from_record(_specs(record), source="digikey", retrieved_at=None)
    assert out["lifecycle"] == {
        "status": "active", "raw": "Active", "discontinued": False, "end_of_life": False,
        "source": "digikey", "refreshed_at": None,
    }
    assert out["compliance"] == {
        "rohs": "ROHS3 Compliant", "reach": "REACH Unaffected", "eccn": "EAR99",
        "hts": "8542.39.00.01", "msl": "1 (Unlimited)", "source": "digikey",
        "refreshed_at": None,
    }


def test_digikey_flags_without_a_status_still_say_discontinued():
    product = {**_stub_product(), "ProductStatus": None, "Discontinued": True,
               "EndOfLife": True, "Ncnr": True}
    out = lc.from_record(_specs(_record_from_product(product)), source="digikey",
                         retrieved_at=None)
    life = out["lifecycle"]
    assert life["status"] == "discontinued" and "raw" not in life
    assert life["discontinued"] is True and life["end_of_life"] is True
    assert life["ncnr"] is True


def test_recorded_mouser_part_maps_to_structured_fields(monkeypatch):
    monkeypatch.setattr("app.domain.parts.providers.mouser._post_mouser",
                        lambda url, payload: _stub_full_response())
    result = MouserProvider("k").lookup_mpn("RC0402JR-070R")["result"]
    out = lc.from_record(_specs(result), source="mouser", retrieved_at=None)
    assert out["lifecycle"] == {
        "status": "active", "raw": "Production", "discontinued": False,
        "end_of_life": False, "source": "mouser", "refreshed_at": None,
        "suggested_replacement": "RC0402JR-070R-NEW",
    }
    # REACH from ProductCompliance wins over the REACH-SVHC substance list;
    # Mouser sent no ECCN / HTS / MSL in this response.
    assert out["compliance"] == {
        "rohs": "RoHS Compliant", "reach": "Compliant", "eccn": "unknown",
        "hts": "unknown", "msl": "unknown", "source": "mouser", "refreshed_at": None,
    }


def test_mouser_compliance_entries_fill_eccn_and_hts(monkeypatch):
    payload = _stub_full_response()
    part = payload["SearchResults"]["Parts"][0]
    part["ProductCompliance"] = [
        {"ComplianceName": "USHTS", "ComplianceValue": "8533210030"},
        {"ComplianceName": "ECCN", "ComplianceValue": "EAR99"},
    ]
    part["IsDiscontinued"] = "True"
    part["LifecycleStatus"] = "Obsolete"
    monkeypatch.setattr("app.domain.parts.providers.mouser._post_mouser",
                        lambda url, body: payload)
    result = MouserProvider("k").lookup_mpn("RC0402JR-070R")["result"]
    out = lc.from_record(_specs(result), source="mouser", retrieved_at=None)
    assert out["compliance"]["hts"] == "8533210030"
    assert out["compliance"]["eccn"] == "EAR99"
    assert out["compliance"]["reach"] == "Lead, Cadmium", "REACH SVHC as the fallback"
    assert out["lifecycle"]["status"] == "obsolete"
    assert out["lifecycle"]["discontinued"] is True


def test_an_empty_record_is_unknown_and_says_the_provider_was_silent():
    out = lc.from_record({"Resistance": "10k"}, source="digikey", retrieved_at=None)
    assert out["lifecycle"] == {"status": "unknown", "discontinued": "unknown",
                                "end_of_life": "unknown", "source": "digikey",
                                "refreshed_at": None, "reason": "provider_silent",
                                "note": lc.SILENT_NOTE_DEFAULT}
    comp = out["compliance"]
    assert comp["reason"] == "provider_silent" and comp["source"] == "digikey"
    assert {comp[k] for k in lc.COMPLIANCE_KEYS} == {"unknown"}


def test_an_unmapped_status_is_unrecognised_and_keeps_raw():
    stamp = datetime(2026, 11, 25, 8, 0, tzinfo=UTC)
    out = lc.from_record({"Lifecycle": "Factory Special Order"}, source="digikey",
                         retrieved_at=stamp)
    life = out["lifecycle"]
    assert life["status"] == "unknown" and life["raw"] == "Factory Special Order"
    assert life["reason"] == "unrecognised" and "note" not in life
    assert life["source"] == "digikey" and life["refreshed_at"] == stamp.isoformat()


def test_a_live_mouser_record_with_no_lifecycle_status_is_provider_silent(monkeypatch):
    """Mouser sends `LifecycleStatus: null` for parts in normal production
    (`fixtures/catalog_search/mouser_keyword.json`, the in-stock Murata
    MLCC); the adapter drops it, so the record has no Lifecycle at all."""
    payload = _stub_full_response()
    payload["SearchResults"]["Parts"][0]["LifecycleStatus"] = None
    monkeypatch.setattr("app.domain.parts.providers.mouser._post_mouser",
                        lambda url, body: payload)
    result = MouserProvider("k").lookup_mpn("RC0402JR-070R")["result"]
    assert "Lifecycle" not in _specs(result), "an empty LifecycleStatus is not stored"
    stamp = datetime(2026, 11, 25, 8, 0, tzinfo=UTC)
    out = lc.from_record(_specs(result), source="mouser", retrieved_at=stamp)
    life = out["lifecycle"]
    assert life["status"] == "unknown", "silence is never inferred as active"
    assert life["reason"] == "provider_silent"
    assert life["note"] == lc.SILENT_NOTES[("lifecycle", "mouser")]
    assert life["source"] == "mouser" and life["refreshed_at"] == stamp.isoformat()
    assert "reason" not in out["compliance"], "RoHS answered, so compliance is known"


def test_the_fixture_shows_mouser_leaving_lifecycle_empty_for_a_stocked_part():
    parts = json.loads((Path(__file__).parent / "fixtures" / "catalog_search"
                        / "mouser_keyword.json").read_text())["SearchResults"]["Parts"]
    stocked = [p for p in parts if "In Stock" in (p.get("Availability") or "")]
    assert any(p.get("LifecycleStatus") is None for p in stocked)


# ---------------------------------------------------------------------------
# stored rows (get_part, export_parts) — written by the real refresh path
# ---------------------------------------------------------------------------
_DK = {
    "ManufacturerProductNumber": MPN,
    "Manufacturer": {"Name": "Vishay Siliconix"},
    "Description": {"ProductDescription": "MOSFET P-CH 60V 50A TO252"},
    "Category": {"Name": "Transistors"},
    "ProductUrl": "https://www.digikey.com/p/sud50p06",
    "ProductStatus": {"Status": "Active"},
    "Classifications": {
        "RohsStatus": "ROHS3 Compliant",
        "ReachStatus": "REACH Unaffected",
        "MoistureSensitivityLevel": "1 (Unlimited)",
        "ExportControlClassNumber": "EAR99",
        "HtsusCode": "8541.29.0095",
    },
}
_MOUSER = {
    "Manufacturer": "Vishay",
    "ManufacturerPartNumber": MPN,
    "Description": "MOSFET P-CH 60V",
    "Category": "MOSFETs",
    "ProductDetailUrl": "https://www.mouser.com/p/sud50p06",
    "LifecycleStatus": "Obsolete",
    "ROHSStatus": "RoHS Compliant",
    "IsDiscontinued": "True",
}


@pytest.fixture
def authed() -> TestClient:
    c = TestClient(app)
    signup_user(c)
    return c


def _refreshed_part(c: TestClient, monkeypatch, *, mouser: bool = True) -> str:
    _enable_digikey_primary(c)
    _stub_digikey(monkeypatch, _DK)
    part_id = _create_part(c, name="P-MOSFET", mpn=MPN)
    r = c.post(f"/api/parts/{part_id}/refresh-from-provider")
    assert r.status_code == 200, r.text
    if mouser:
        _configure_mouser_secondary(c)
        _stub_mouser(monkeypatch, _MOUSER)
        r = c.post(f"/api/parts/{part_id}/refresh-from-provider?provider=mouser")
        assert r.status_code == 200, r.text
    return part_id


async def _get_part(c: TestClient, part_id: str) -> dict:
    async with mcp_session(_mint(c, read_only=True)["token"]) as s:
        return await call(s, "get_part", id_or_mpn=part_id)


async def test_get_part_precedence_digikey_answers_and_mouser_is_an_alternate(
    authed, monkeypatch
):
    part_id = _refreshed_part(authed, monkeypatch)
    out = await _get_part(authed, part_id)

    life = out["lifecycle"]
    assert life["status"] == "active" and life["raw"] == "Active"
    assert life["source"] == "digikey"
    assert life["discontinued"] is False and life["end_of_life"] is False
    assert life["refreshed_at"]
    assert "reason" not in life and "note" not in life, "a known status needs no reason"
    [alt] = life["alternates"]
    assert alt["source"] == "mouser" and alt["status"] == "obsolete"
    assert alt["raw"] == "Obsolete" and alt["discontinued"] is True
    assert alt["refreshed_at"]

    comp = out["compliance"]
    assert comp["source"] == "digikey"
    assert comp["rohs"] == "ROHS3 Compliant" and comp["reach"] == "REACH Unaffected"
    # ADR-0034 retires ECCN / HTS / MSL as junk: never stored, so unknown.
    assert comp["eccn"] == comp["hts"] == comp["msl"] == "unknown"
    assert comp["alternates"] == [{"source": "mouser", "rohs": "RoHS Compliant",
                                   "refreshed_at": comp["alternates"][0]["refreshed_at"]}]


async def test_get_part_reads_the_stored_rows_and_writes_none(authed, monkeypatch, db):
    part_id = _refreshed_part(authed, monkeypatch)
    count = select(func.count()).select_from(CustomField)
    before = db.execute(count).scalar_one()
    rows = {r.key: r for r in db.execute(
        select(CustomField).where(CustomField.object_id == uuid.UUID(part_id))
    ).scalars()}
    # The values come from these rows — the primary's bare catalog key and
    # the secondary's namespaced one — not from a second copy.
    assert rows["Lifecycle"].value == "Active" and rows["Lifecycle"].source == "provider"
    assert rows["mouser:Lifecycle"].value == "Obsolete"

    await _get_part(authed, part_id)
    assert db.execute(count).scalar_one() == before


async def test_a_manual_value_is_marked_manual_and_outranks_providers(authed, monkeypatch):
    part_id = _refreshed_part(authed, monkeypatch, mouser=False)
    # The user edits the provider's lifecycle row (it becomes an `override`)
    # and enters an ECCN by hand, which no provider row carries.
    for key, value in (("Lifecycle", "NRND"), ("ECCN", "3A991")):
        r = authed.post("/api/custom-fields", json={
            "object_type": "part", "object_id": part_id, "key": key, "value": value,
        })
        assert r.status_code in (200, 201), r.text

    out = await _get_part(authed, part_id)
    life = out["lifecycle"]
    assert life["source"] == "manual" and life["status"] == "nrnd" and life["raw"] == "NRND"
    assert life["discontinued"] == "unknown", "a manual source asserts no flags"
    comp = out["compliance"]
    assert comp["source"] == "manual" and comp["eccn"] == "3A991"
    assert comp["rohs"] == "unknown"
    [alt] = comp["alternates"]
    assert alt["source"] == "digikey" and alt["rohs"] == "ROHS3 Compliant"


async def test_a_part_no_provider_touched_is_never_fetched(authed):
    part_id = _create_part(authed, name="Bare", mpn=None)
    out = await _get_part(authed, part_id)
    assert out["lifecycle"] == {"status": "unknown", "discontinued": "unknown",
                                "end_of_life": "unknown", "source": "unknown",
                                "refreshed_at": None, "reason": "never_fetched"}
    assert out["compliance"] == {"rohs": "unknown", "reach": "unknown", "eccn": "unknown",
                                 "hts": "unknown", "msl": "unknown", "source": "unknown",
                                 "refreshed_at": None, "reason": "never_fetched"}


# A Mouser part in normal production: Mouser sends no LifecycleStatus.
_MOUSER_SILENT = {**_MOUSER, "LifecycleStatus": None, "IsDiscontinued": "False"}


def _mouser_only_part(c: TestClient, monkeypatch, part: dict) -> str:
    """Linked to Mouser (a secondary) and nothing else — no primary link."""
    _configure_mouser_secondary(c)
    _stub_mouser(monkeypatch, part)
    part_id = _create_part(c, name="Mouser only", mpn=MPN)
    r = c.post(f"/api/parts/{part_id}/refresh-from-provider?provider=mouser")
    assert r.status_code == 200, r.text
    return part_id


def _link_stamp(db, part_id: str, provider: str) -> str:
    link = db.execute(select(PartProviderLink).where(
        PartProviderLink.part_id == uuid.UUID(part_id),
        PartProviderLink.provider == provider,
    )).scalar_one()
    return link.last_refresh_at.isoformat()


async def test_a_mouser_only_part_with_no_lifecycle_is_provider_silent(
    authed, monkeypatch, db
):
    part_id = _mouser_only_part(authed, monkeypatch, _MOUSER_SILENT)
    out = await _get_part(authed, part_id)

    life = out["lifecycle"]
    assert life["status"] == "unknown", "Mouser silence is not inferred as active"
    assert life["reason"] == "provider_silent"
    assert life["source"] == "mouser"
    assert life["refreshed_at"] == _link_stamp(db, part_id, "mouser")
    assert life["note"] == ("Mouser states lifecycle only for parts that are not "
                            "in normal production")
    assert "raw" not in life
    # Mouser did send RoHS — compliance is answered and needs no reason.
    comp = out["compliance"]
    assert comp["source"] == "mouser" and comp["rohs"] == "RoHS Compliant"
    assert "reason" not in comp


async def test_a_stored_unmapped_status_is_unrecognised(authed, monkeypatch):
    part_id = _mouser_only_part(
        authed, monkeypatch,
        {**_MOUSER_SILENT, "LifecycleStatus": "Factory Special Order"},
    )
    life = (await _get_part(authed, part_id))["lifecycle"]
    assert life["status"] == "unknown" and life["raw"] == "Factory Special Order"
    assert life["reason"] == "unrecognised" and life["source"] == "mouser"
    assert "note" not in life


async def test_export_parts_carries_the_reasons_without_per_part_queries(
    authed, monkeypatch, db
):
    silent = _mouser_only_part(authed, monkeypatch, _MOUSER_SILENT)
    bare = _create_part(authed, name="Bare", mpn=None)
    singles = {pid: await _get_part(authed, pid) for pid in (silent, bare)}
    async with mcp_session(_mint(authed, read_only=True)["token"]) as s:
        page = await call(s, "export_parts")
    items = {i["id"]: i for i in page["items"]}
    for pid, single in singles.items():
        assert items[pid]["lifecycle"] == single["lifecycle"]
        assert items[pid]["compliance"] == single["compliance"]
    assert items[silent]["lifecycle"]["reason"] == "provider_silent"
    assert items[bare]["lifecycle"]["reason"] == "never_fetched"


def test_for_parts_runs_two_queries_whatever_the_part_count(authed, monkeypatch, db):
    from app.domain.workspaces.models import Workspace

    ids = [uuid.UUID(_mouser_only_part(authed, monkeypatch, _MOUSER_SILENT))]
    ids += [uuid.UUID(_create_part(authed, name=f"Bare {n}", mpn=None)) for n in range(3)]
    ws = db.get(Workspace, uuid.UUID(
        authed.get("/api/auth/me").json()["data"]["workspaces"][0]["id"]))
    calls = []
    real = db.execute
    monkeypatch.setattr(db, "execute", lambda *a, **kw: calls.append(a) or real(*a, **kw))
    out = lc.for_parts(db, ws, ids)
    assert len(calls) == 2
    assert [out[i]["lifecycle"]["reason"] for i in ids] == [
        "provider_silent", "never_fetched", "never_fetched", "never_fetched"]


async def test_export_parts_carries_the_same_blocks(authed, monkeypatch):
    part_id = _refreshed_part(authed, monkeypatch)
    single = await _get_part(authed, part_id)
    async with mcp_session(_mint(authed, read_only=True)["token"]) as s:
        page = await call(s, "export_parts")
    [item] = [i for i in page["items"] if i["id"] == part_id]
    assert item["lifecycle"] == single["lifecycle"]
    assert item["compliance"] == single["compliance"]


async def test_another_workspaces_rows_never_answer(authed, monkeypatch, db):
    mine = _refreshed_part(authed, monkeypatch, mouser=False)
    other = TestClient(app)
    signup_user(other)
    theirs = _create_part(other, name="Theirs", mpn="OTHER-1")
    # A foreign row pointing at MY part id (the DB has no FK on object_id):
    # workspace filtering, not the id, has to keep it out.
    their_ws = uuid.UUID(other.get("/api/auth/me").json()["data"]["workspaces"][0]["id"])
    for object_id in (uuid.UUID(mine), uuid.UUID(theirs)):
        db.add(CustomField(workspace_id=their_ws, object_type="part", object_id=object_id,
                           key="mouser:Lifecycle", value="Obsolete", source="provider",
                           provider="mouser"))
    db.flush()

    out = await _get_part(authed, mine)
    assert out["lifecycle"]["source"] == "digikey"
    assert "alternates" not in out["lifecycle"]
