"""SM-009 over MCP: upload parses, set_spice_binding validates and derives
`sim_pins`, get_part_eda reports, list_spice_binding_issues flags.

Acceptance map (edaflow modules/stockmanager.md SM-009):
* `.subckt OPA192 IN+ IN- V+ V- OUT` stores subckt + 5 ordered ports
  → test_upload_records_subckt_and_ports_unvalidated
* unmapped pin / missing port refused with a stable code
  → test_bad_pin_maps_are_refused_and_write_nothing
* HTTP library still emits Sim.Device/Pins/Params/Library
  → test_http_library_emits_the_derived_sim_pins_under_the_same_names
* existing free-text values readable and flagged unvalidated
  → test_free_text_sim_pins_is_reported_unvalidated_and_left_alone
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domain.audit.models import AuditLog
from app.domain.eda.models import PartEda, PartSpiceBinding
from app.main import app
from tests._factories import create_part, signup_user
from tests.test_kicad_api import _part as _kicad_part
from tests.test_kicad_api import _token_client
from tests.test_mcp import _b64, _mint, call, call_error, mcp_session

FIXTURES = Path(__file__).parent / "fixtures" / "spice"
OPA_PIN_MAP = {"1": "OUT", "2": "V-", "3": "IN+", "4": "IN-", "5": "V+"}
OPA_SIM_PINS = "1=OUT 2=V- 3=IN+ 4=IN- 5=V+"


@pytest.fixture
def full_token(authed_client) -> str:
    return _mint(authed_client)["token"]


@pytest.fixture
def readonly_token(authed_client) -> str:
    return _mint(authed_client, read_only=True)["token"]


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


async def _opa_part(s, client: TestClient, *, mpn: str = "OPA192IDBVR") -> str:
    """A part with the hosted OPA192 symbol and the OPA192 model attached."""
    part_id = create_part(client, "OPA192 op-amp", mpn=mpn)
    await call(
        s,
        "upload_eda_asset",
        kind="symbol",
        filename="OPA192.kicad_sym",
        content_base64=_b64(_fixture("opa192.kicad_sym")),
        part_id=part_id,
    )
    return part_id


async def _upload_opa_model(s, part_id: str, name: str = "OPA192.lib", raw: bytes | None = None):
    return await call(
        s,
        "upload_eda_asset",
        kind="spice",
        filename=name,
        content_base64=_b64(raw or _fixture("opa192_continuation.lib")),
        part_id=part_id,
    )


async def test_upload_records_subckt_and_ports_unvalidated(authed_client, full_token):
    async with mcp_session(full_token) as s:
        part_id = await _opa_part(s, authed_client)
        uploaded = await _upload_opa_model(s, part_id)
        eda = await call(s, "get_part_eda", part_id=part_id)

    assert uploaded["spice_models"] == [
        {"name": "OPA192", "kind": "subckt", "ports": ["IN+", "IN-", "V+", "V-", "OUT"]}
    ]
    assert uploaded["spice_binding_updated"] is True
    binding = eda["spice_binding"]
    assert binding["source"] == "binding"
    assert binding["subckt"] == "OPA192"
    assert binding["ports"] == ["IN+", "IN-", "V+", "V-", "OUT"]
    assert binding["validated"] is False
    assert binding["issues"] == ["not_confirmed"]
    assert binding["model_sha256"] == uploaded["sha256"]
    assert binding["pin_map"] is None
    # Nothing was written into sim_pins by the upload.
    assert "sim_pins" not in eda


async def test_set_spice_binding_validates_derives_sim_pins_and_audits(
    authed_client, full_token, db
):
    async with mcp_session(full_token) as s:
        part_id = await _opa_part(s, authed_client)
        await _upload_opa_model(s, part_id)
        result = await call(
            s, "set_spice_binding", part_id_or_mpn="OPA192IDBVR", pin_map=OPA_PIN_MAP
        )
        eda = await call(s, "get_part_eda", part_id=part_id)

    assert result["sim_pins"] == OPA_SIM_PINS
    assert result["warnings"] == []
    binding = eda["spice_binding"]
    assert binding["validated"] is True
    assert binding["issues"] == []
    assert binding["pin_map"] == OPA_PIN_MAP
    assert binding["symbol_pins_source"] == "hosted_symbol"
    assert eda["sim_pins"] == OPA_SIM_PINS

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.action == "part_spice_binding.set")
        .all()
    )
    assert len(rows) == 1
    assert part_id in [str(t) for t in rows[0].target_ids]
    assert rows[0].comment.startswith("kind=subckt,subckt=OPA192,ports=5,sha256=")


async def test_bad_pin_maps_are_refused_and_write_nothing(authed_client, full_token, db):
    async with mcp_session(full_token) as s:
        part_id = await _opa_part(s, authed_client)
        await _upload_opa_model(s, part_id)
        unmapped = await call_error(
            s,
            "set_spice_binding",
            part_id_or_mpn=part_id,
            pin_map={k: v for k, v in OPA_PIN_MAP.items() if k != "5"},
        )
        missing_port = await call_error(
            s,
            "set_spice_binding",
            part_id_or_mpn=part_id,
            pin_map={**OPA_PIN_MAP, "5": "VCC"},
        )
        count = await call_error(
            s,
            "set_spice_binding",
            part_id_or_mpn=part_id,
            pin_map={**OPA_PIN_MAP, "5": None},
        )
        conflict = await call_error(
            s,
            "set_spice_binding",
            part_id_or_mpn=part_id,
            pin_map=OPA_PIN_MAP,
            symbol_pins=["1", "2", "3"],
        )

    assert "eda.spice_pin_unmapped:" in unmapped
    assert "eda.spice_port_unknown:" in missing_port
    assert "eda.spice_port_count_mismatch:" in count
    assert "eda.spice_symbol_pins_conflict:" in conflict
    db.expire_all()
    binding = db.query(PartSpiceBinding).filter_by(part_id=uuid.UUID(part_id)).one()
    assert binding.validated is False and binding.pin_map is None
    assert db.query(PartEda).filter_by(part_id=uuid.UUID(part_id)).one().sim_pins is None


async def test_multi_subckt_file_needs_an_explicit_choice(authed_client, full_token):
    part_id = create_part(authed_client, "Dual-model part")
    async with mcp_session(full_token) as s:
        uploaded = await call(
            s,
            "upload_eda_asset",
            kind="spice",
            filename="two.lib",
            content_base64=_b64(_fixture("two_opamps.lib")),
            part_id=part_id,
        )
        # Two models: the upload records nothing, it cannot know which.
        before = await call(s, "get_part_eda", part_id=part_id)
        ambiguous = await call_error(
            s, "set_spice_binding", part_id_or_mpn=part_id,
            pin_map={"1": "1"}, symbol_pins=["1"],
        )
        unknown = await call_error(
            s, "set_spice_binding", part_id_or_mpn=part_id, subckt="OPA_C",
            pin_map={"1": "1"}, symbol_pins=["1"],
        )
        no_pins = await call_error(
            s, "set_spice_binding", part_id_or_mpn=part_id, subckt="OPA_B",
            pin_map={"1": "1"},
        )
        chosen = await call(
            s, "set_spice_binding", part_id_or_mpn=part_id, subckt="opa_b",
            pin_map={"1": "1", "2": "2", "3": "3", "4": "4", "5": "5", "6": None},
            symbol_pins=["1", "2", "3", "4", "5", "6"],
        )

    assert uploaded["spice_binding_updated"] is False
    assert [m["name"] for m in uploaded["spice_models"]] == ["OPA_A", "OPA_B"]
    assert before["spice_binding"]["state"] == "unbound"
    assert before["spice_binding"]["spice_name"] == "two"
    assert "eda.spice_subckt_ambiguous:" in ambiguous
    assert "OPA_A" in ambiguous and "OPA_B" in ambiguous
    assert "eda.spice_subckt_unknown:" in unknown
    assert "eda.spice_symbol_pins_unknown:" in no_pins
    assert chosen["sim_pins"] == "1=1 2=2 3=3 4=4 5=5"
    assert chosen["spice_binding"]["subckt"] == "OPA_B"
    assert chosen["spice_binding"]["symbol_pins_source"] == "declared"


async def test_ng010_model_card_binding_carries_header_metadata(authed_client, full_token):
    part_id = create_part(authed_client, "1N4148 diode")
    async with mcp_session(full_token) as s:
        uploaded = await call(
            s,
            "upload_eda_asset",
            kind="spice",
            filename="1N4148.lib",
            content_base64=_b64(_fixture("d1n4148_ng010.lib")),
            part_id=part_id,
        )
        result = await call(
            s, "set_spice_binding", part_id_or_mpn=part_id,
            pin_map={"1": "K", "2": "A"}, symbol_pins=["1", "2"],
        )
        not_checkable_part = create_part(authed_client, "Resistor card")
        await call(
            s, "upload_eda_asset", kind="spice", filename="models_only.lib",
            content_base64=_b64(_fixture("models_only.lib")), part_id=not_checkable_part,
        )
        refused = await call_error(
            s, "set_spice_binding", part_id_or_mpn=not_checkable_part, subckt="RMOD",
            pin_map={"1": "A"}, symbol_pins=["1"],
        )

    assert uploaded["ng010_header"]["catalogue_entry"] == "1N4148"
    assert uploaded["ng010_header"]["pins"] == ["A", "K"]
    binding = result["spice_binding"]
    assert result["sim_pins"] == "1=K 2=A"
    assert binding["model_kind"] == "model" and binding["device_type"] == "D"
    assert binding["ports"] == ["A", "K"]
    assert binding["model_source"].startswith("ngspice-library:1N4148; lib/cmp/standard_dio.db")
    assert uploaded["ng010_header"]["build_needs"] == "stock"
    assert binding["build_requirement"].startswith("needs stock")
    assert binding["grade"].startswith("valid_for=['dc']")
    assert binding["header_pins"] == ["A", "K"]
    assert "eda.spice_model_not_checkable:" in refused


async def test_new_file_version_unvalidates_without_deleting(authed_client, full_token, db):
    async with mcp_session(full_token) as s:
        part_id = await _opa_part(s, authed_client)
        await _upload_opa_model(s, part_id)
        await call(s, "set_spice_binding", part_id_or_mpn=part_id, pin_map=OPA_PIN_MAP)
        eda = await call(s, "get_part_eda", part_id=part_id)
        # Detach the model (set_part_eda replaces the configuration; the
        # symbol is passed back) and upload a new version of the file.
        await call(s, "set_part_eda", part_id=part_id, symbol_id=eda["symbol_id"])
        detached = await call(s, "get_part_eda", part_id=part_id)
        v2 = _fixture("opa192_continuation.lib") + b"* revision 2\n"
        uploaded = await _upload_opa_model(s, part_id, name="OPA192_v2.lib", raw=v2)
        stale = await call(s, "get_part_eda", part_id=part_id)
        issues = await call(s, "list_spice_binding_issues")
        again = await call(s, "set_spice_binding", part_id_or_mpn=part_id, pin_map=OPA_PIN_MAP)

    assert detached["spice_binding"]["issues"] == ["model_detached", "sim_pins_edited"]
    assert uploaded["spice_binding_updated"] is True
    binding = stale["spice_binding"]
    assert binding["validated"] is False
    assert binding["issues"] == ["awaiting_reconfirmation"]
    assert binding["pin_map"] == OPA_PIN_MAP  # kept for re-confirmation
    assert binding["model_sha256"] == uploaded["sha256"]
    assert [i["part_id"] for i in issues["items"]] == [part_id]
    assert again["spice_binding"]["validated"] is True
    assert again["sim_pins"] == OPA_SIM_PINS
    assert db.query(AuditLog).filter(
        AuditLog.action == "part_spice_binding.model_recorded"
    ).count() == 2


async def test_hand_edited_sim_pins_unvalidates_the_binding(authed_client, full_token):
    async with mcp_session(full_token) as s:
        part_id = await _opa_part(s, authed_client)
        uploaded = await _upload_opa_model(s, part_id)
        await call(s, "set_spice_binding", part_id_or_mpn=part_id, pin_map=OPA_PIN_MAP)
        eda = await call(s, "get_part_eda", part_id=part_id)
        r = authed_client.put(
            f"/api/parts/{part_id}/eda",
            json={
                "symbol_id": eda["symbol_id"],
                "spice_datafile_id": uploaded["id"],
                "sim_pins": "1=IN+ 2=IN- 3=OUT 4=V- 5=V+",
            },
        )
        assert r.status_code == 200, r.text
        edited = await call(s, "get_part_eda", part_id=part_id)

    assert edited["spice_binding"]["validated"] is False
    assert edited["spice_binding"]["issues"] == ["sim_pins_edited"]
    assert edited["sim_pins"] == "1=IN+ 2=IN- 3=OUT 4=V- 5=V+"


async def test_http_library_emits_the_derived_sim_pins_under_the_same_names(
    authed_client, full_token
):
    async with mcp_session(full_token) as s:
        part_id = await _opa_part(s, authed_client)
        uploaded = await _upload_opa_model(s, part_id)
        await call(s, "set_spice_binding", part_id_or_mpn=part_id, pin_map=OPA_PIN_MAP)
        eda = await call(s, "get_part_eda", part_id=part_id)
    r = authed_client.put(
        f"/api/parts/{part_id}/eda",
        json={
            "symbol_id": eda["symbol_id"],
            "spice_datafile_id": uploaded["id"],
            "exclude_from_sim": False,
            "sim_device": "SUBCKT",
            "sim_pins": eda["sim_pins"],
            "sim_params": "GAIN=1e5",
        },
    )
    assert r.status_code == 200, r.text

    kicad = _token_client(_mint(authed_client, read_only=True)["token"])
    fields = kicad.get(_kicad_part(part_id)).json()["fields"]
    assert fields["Sim.Device"]["value"] == "SUBCKT"
    assert fields["Sim.Pins"]["value"] == OPA_SIM_PINS
    assert fields["Sim.Params"]["value"] == "GAIN=1e5"
    assert fields["Sim.Library"]["value"].startswith("${STOCKMGR_SPICE}/")


async def test_free_text_sim_pins_is_reported_unvalidated_and_left_alone(
    authed_client, full_token, db
):
    async with mcp_session(full_token) as s:
        part_id = create_part(authed_client, "Legacy op-amp")
        uploaded = await call(
            s, "upload_eda_asset", kind="spice", filename="legacy.lib",
            content_base64=_b64(b".subckt LEG IN+ IN- V+ V- OUT\n.ends\n"),
        )
        r = authed_client.put(
            f"/api/parts/{part_id}/eda",
            json={
                "symbol_ref_external": "Amplifier_Operational:OPA192",
                "spice_datafile_id": uploaded["id"],
                "sim_pins": "1=IN+ 2=IN- 3=OUT 4=GND",
            },
        )
        assert r.status_code == 200, r.text
        no_model = create_part(authed_client, "Pins, no model")
        r = authed_client.put(f"/api/parts/{no_model}/eda", json={"sim_pins": "1=+ 2=-"})
        assert r.status_code == 200, r.text

        eda = await call(s, "get_part_eda", part_id=part_id)
        issues = await call(s, "list_spice_binding_issues")

    assert eda["sim_pins"] == "1=IN+ 2=IN- 3=OUT 4=GND"
    assert eda["spice_binding"] == {
        "source": "free-text",
        "state": "free_text",
        "validated": False,
        "sim_pins": "1=IN+ 2=IN- 3=OUT 4=GND",
    }
    by_part = {i["part_id"]: i for i in issues["items"]}
    legacy = by_part[part_id]
    assert legacy["source"] == "free-text"
    assert legacy["issues"] == ["free_text_unvalidated", "port_count_mismatch", "port_unknown"]
    assert legacy["ports"] == ["IN+", "IN-", "V+", "V-", "OUT"]
    assert legacy["unknown_ports"] == ["GND"]
    assert by_part[no_model]["issues"] == ["free_text_unvalidated", "no_model_attached"]
    # Read-only: no binding row was created and sim_pins is untouched.
    db.expire_all()
    assert db.query(PartSpiceBinding).count() == 0
    assert db.query(PartEda).filter_by(part_id=uuid.UUID(part_id)).one().sim_pins == (
        "1=IN+ 2=IN- 3=OUT 4=GND"
    )


async def test_issue_list_pages_with_its_own_cursor(authed_client, full_token):
    ids = []
    for i in range(5):
        part_id = create_part(authed_client, f"Free text {i}")
        r = authed_client.put(f"/api/parts/{part_id}/eda", json={"sim_pins": "1=+ 2=-"})
        assert r.status_code == 200, r.text
        ids.append(part_id)
    async with mcp_session(full_token) as s:
        first = await call(s, "list_spice_binding_issues", page_size=2)
        second = await call(
            s, "list_spice_binding_issues", page_size=2, cursor=first["next_cursor"]
        )
        third = await call(
            s, "list_spice_binding_issues", page_size=2, cursor=second["next_cursor"]
        )
        export_page = await call(s, "export_parts", page_size=1)
        foreign = await call_error(
            s, "list_spice_binding_issues", cursor=export_page["next_cursor"]
        )
        clamped = await call(s, "list_spice_binding_issues", page_size=1000)

    seen = [i["part_id"] for page in (first, second, third) for i in page["items"]]
    assert sorted(seen) == sorted(ids) and len(set(seen)) == 5
    assert third["next_cursor"] is None
    assert "mcp.invalid_cursor:" in foreign
    assert clamped["page_size"] == 100


async def test_workspace_isolation(authed_client, full_token):
    async with mcp_session(full_token) as s:
        part_id = await _opa_part(s, authed_client)
        await _upload_opa_model(s, part_id)
        await call(s, "set_spice_binding", part_id_or_mpn=part_id, pin_map=OPA_PIN_MAP)

    other = TestClient(app)
    signup_user(other)
    other_token = _mint(other)["token"]
    async with mcp_session(other_token) as s:
        by_id = await call_error(
            s, "set_spice_binding", part_id_or_mpn=part_id, pin_map=OPA_PIN_MAP
        )
        by_mpn = await call_error(
            s, "set_spice_binding", part_id_or_mpn="OPA192IDBVR", pin_map=OPA_PIN_MAP
        )
        read = await call_error(s, "get_part_eda", part_id=part_id)
        issues = await call(s, "list_spice_binding_issues")

    assert "not_found" in by_id and "not_found" in by_mpn and "not_found" in read
    assert issues["items"] == []


async def test_read_only_token_is_refused(authed_client, full_token, readonly_token):
    async with mcp_session(full_token) as s:
        part_id = await _opa_part(s, authed_client)
        await _upload_opa_model(s, part_id)
    async with mcp_session(readonly_token) as s:
        message = await call_error(
            s, "set_spice_binding", part_id_or_mpn=part_id, pin_map=OPA_PIN_MAP
        )
        listed = await call(s, "list_spice_binding_issues")
    assert "auth.token_read_only:" in message
    assert [i["part_id"] for i in listed["items"]] == [part_id]


# ---------------------------------------------------------------------
# File-less kinds and unconnected ports (ngSPICE inventory follow-up)
# ---------------------------------------------------------------------


async def test_native_primitive_and_no_model_are_first_class(authed_client, full_token, db):
    create_part(authed_client, "10k 0402", mpn="RC0402-10K")
    capacitor = create_part(authed_client, "100n 0402")
    regulator = create_part(authed_client, "Buck without model")
    async with mcp_session(full_token) as s:
        r = await call(
            s, "set_spice_binding", part_id_or_mpn="RC0402-10K",
            model_kind="native_primitive", device_type="r", params="r=10k",
            pin_map={"1": "+", "2": "-"}, symbol_pins=["1", "2"],
            source="ngspice inventory manifest 8c07aaf",
        )
        c = await call(
            s, "set_spice_binding", part_id_or_mpn=capacitor,
            model_kind="native_primitive", device_type="C", params="c=100n",
        )
        n = await call(
            s, "set_spice_binding", part_id_or_mpn=regulator, model_kind="none",
            fallback="vendor model wanted, or attended LTspice",
        )
        none_with_map = await call_error(
            s, "set_spice_binding", part_id_or_mpn=regulator, model_kind="none",
            pin_map={"1": "+"},
        )
        bad_type = await call_error(
            s, "set_spice_binding", part_id_or_mpn=capacitor,
            model_kind="native_primitive", device_type="Q",
        )
        read = await call(s, "get_part_eda", part_id=regulator)
        issues = await call(s, "list_spice_binding_issues")

    assert r["spice_binding"]["validated"] is True
    assert r["spice_binding"]["model_kind"] == "native_primitive"
    assert r["spice_binding"]["device_type"] == "R"
    assert r["spice_binding"]["ports"] == ["+", "-"]
    assert r["spice_binding"]["sim_pins"] == "1=+ 2=-"
    assert r["spice_binding"]["params"] == "r=10k"
    assert r["spice_binding"]["model_source"] == "ngspice inventory manifest 8c07aaf"
    assert r["sim_pins"] is None  # part_eda untouched: no row was created
    assert c["spice_binding"]["validated"] is False
    assert c["spice_binding"]["issues"] == ["not_confirmed"]
    assert n["spice_binding"]["model_kind"] == "none"
    assert n["spice_binding"]["issues"] == ["no_model"]
    assert n["spice_binding"]["fallback"] == "vendor model wanted, or attended LTspice"
    assert read["configured"] is False
    assert read["spice_binding"]["model_kind"] == "none"
    assert "eda.spice_not_applicable:" in none_with_map
    assert "eda.spice_model_not_checkable:" in bad_type
    # File-less records with no file attached are states, not issues.
    assert issues["items"] == []
    assert db.query(PartEda).count() == 0


async def test_file_less_kind_refused_when_a_file_is_attached_then_upgraded(
    authed_client, full_token
):
    async with mcp_session(full_token) as s:
        part_id = create_part(authed_client, "Ferrite bead")
        await call(
            s, "set_spice_binding", part_id_or_mpn=part_id, model_kind="none",
            fallback="native primitive with parameters from the datasheet",
        )
        uploaded = await call(
            s, "upload_eda_asset", kind="spice", filename="bead.lib",
            content_base64=_b64(_fixture("bead_corrected_runtime_ng010.lib")),
            part_id=part_id,
        )
        upgraded = await call(s, "get_part_eda", part_id=part_id)
        conflict = await call_error(
            s, "set_spice_binding", part_id_or_mpn=part_id, model_kind="none",
        )

    assert uploaded["spice_binding_updated"] is True
    assert uploaded["ng010_header"]["build_needs"] == "patched+runtime"
    binding = upgraded["spice_binding"]
    assert binding["model_kind"] == "subckt"
    assert binding["subckt"] == "BEAD1" and binding["ports"] == ["p", "n"]
    assert binding["issues"] == ["not_confirmed"]
    assert binding["fallback"] is None
    assert "correction NG009-001 (proposed)" in binding["model_source"]
    assert binding["build_requirement"].startswith("needs patched+runtime")
    assert "eda.spice_kind_conflict:" in conflict


async def test_unconnected_model_ports_must_be_listed(authed_client, full_token):
    part_id = create_part(authed_client, "13-port LDO")
    symbol_pins = [str(n) for n in range(1, 18)]  # MSOP-16 + exposed pad
    nc_ports = ["3", "8", "10", "12"]
    pin_map = {pin: None for pin in symbol_pins}
    pin_map.update({p: p for p in map(str, range(1, 14)) if p not in nc_ports})
    async with mcp_session(full_token) as s:
        await call(
            s, "upload_eda_asset", kind="spice", filename="ldo13.lib",
            content_base64=_b64(_fixture("ldo13_nc_ports.lib")), part_id=part_id,
        )
        refused = await call_error(
            s, "set_spice_binding", part_id_or_mpn=part_id, pin_map=pin_map,
            symbol_pins=symbol_pins,
        )
        bound = await call(
            s, "set_spice_binding", part_id_or_mpn=part_id, pin_map=pin_map,
            symbol_pins=symbol_pins, unconnected_ports=nc_ports,
        )

    assert "eda.spice_port_count_mismatch:" in refused
    assert "unbound: 3, 8, 10, 12" in refused
    assert bound["spice_binding"]["validated"] is True
    assert bound["spice_binding"]["unconnected_ports"] == nc_ports
    assert bound["sim_pins"] == "1=1 2=2 4=4 5=5 6=6 7=7 9=9 11=11 13=13"


# ---------------------------------------------------------------------
# ngspice-export/1 (ngSPICE export schema 1)
# ---------------------------------------------------------------------


async def test_schema1_header_fills_the_binding_and_defaults_unconnected_ports(
    authed_client, full_token
):
    part_id = create_part(authed_client, "Schema-1 LDO")
    symbol_pins = [str(n) for n in range(1, 18)]
    pin_map = {pin: None for pin in symbol_pins}
    pin_map.update({p: p for p in ("1", "2", "4", "5", "6", "7", "9", "11", "13")})
    async with mcp_session(full_token) as s:
        uploaded = await call(
            s, "upload_eda_asset", kind="spice", filename="ldo13s1.lib",
            content_base64=_b64(_fixture("ldo13_schema1.lib")), part_id=part_id,
        )
        before = await call(s, "get_part_eda", part_id=part_id)
        bound = await call(
            s, "set_spice_binding", part_id_or_mpn=part_id, pin_map=pin_map,
            symbol_pins=symbol_pins,
        )
        explicit_none = await call_error(
            s, "set_spice_binding", part_id_or_mpn=part_id, pin_map=pin_map,
            symbol_pins=symbol_pins, unconnected_ports=[],
        )

    header = uploaded["ng010_header"]
    assert header["export_schema"] == "ngspice-export/1"
    assert header["internally_unconnected_ports"] == ["3", "8", "10", "12"]
    suggested = before["spice_binding"]
    assert suggested["suggested_unconnected_ports"] == ["3", "8", "10", "12"]
    assert suggested["port_functions"]["1"] == "INFILT"
    assert suggested["port_functions"]["3"] is None
    assert suggested["ports_on_symbol"]["3"] is False
    assert suggested["export_kind"] == "subckt" and suggested["build"] == "patched+runtime"
    assert suggested["valid_for_basis"] == {
        "dc": ["distributor_spec"], "ac-small-signal": ["conversion"],
    }
    assert suggested["match"]["confidence"] == "alias"
    assert suggested["channels"] == {"package": None, "model": None}
    binding = bound["spice_binding"]
    assert binding["validated"] is True
    assert binding["unconnected_ports"] == ["3", "8", "10", "12"]
    assert any(w.startswith("unconnected_ports_from_header") for w in bound["warnings"])
    assert "eda.spice_port_count_mismatch:" in explicit_none


async def test_partial_channels_warn_and_unknown_schema_is_refused(authed_client, full_token, db):
    part_id = create_part(authed_client, "Quad switch")
    pin_map = {str(n): None for n in range(1, 17)}
    pin_map.update({"1": "IN", "2": "S", "3": "D", "4": "Vss", "13": "Vdd", "5": "GND"})
    async with mcp_session(full_token) as s:
        await call(
            s, "upload_eda_asset", kind="spice", filename="sw4.lib",
            content_base64=_b64(_fixture("quad_switch_schema1.lib")), part_id=part_id,
        )
        bound = await call(
            s, "set_spice_binding", part_id_or_mpn=part_id, pin_map=pin_map,
            symbol_pins=[str(n) for n in range(1, 17)],
        )
        refused = await call_error(
            s, "upload_eda_asset", kind="spice", filename="sw4v2.lib",
            content_base64=_b64(_fixture("schema2_unsupported.lib")),
        )

    # Port FUNCTIONS from the export header stand in for the numbered ports.
    assert bound["sim_pins"] == "1=1 2=2 3=3 4=4 5=6 13=5"
    assert bound["spice_binding"]["pin_map"]["5"] == "6"
    assert bound["spice_binding"]["channels"] == {"package": 4, "model": 1}
    assert any(w.startswith("partial_channels") for w in bound["warnings"])
    assert "eda.spice_export_schema_unsupported:" in refused
    from app.domain.eda.models import EdaDatafile

    assert db.query(EdaDatafile).filter(EdaDatafile.name == "sw4v2").count() == 0


async def test_manifest_native_and_fallback_records(authed_client, full_token):
    cap = create_part(authed_client, "100p C0G")
    pptc = create_part(authed_client, "PPTC fuse")
    tvs = create_part(authed_client, "TVS diode")
    async with mcp_session(full_token) as s:
        c = await call(
            s, "set_spice_binding", part_id_or_mpn=cap, model_kind="native_primitive",
            native={
                "letter": "C",
                "parameters": {"value": 1e-10, "unit": "F", "source": "inventory snapshot"},
                "parameters_reason": None,
                "loses_behaviour": False,
                "loses_behaviour_reason": None,
            },
            fallback_token="native", fallback="native SPICE primitive; C + ESR + ESL",
        )
        d = await call(
            s, "set_spice_binding", part_id_or_mpn=tvs, model_kind="native_primitive",
            native={
                "letter": "D", "parameters": None,
                "parameters_reason": "no unambiguous value in the inventory snapshot",
                "loses_behaviour": True,
                "loses_behaviour_reason": "partial: generic .model D (DC Vf roughly)",
            },
        )
        n = await call(
            s, "set_spice_binding", part_id_or_mpn=pptc, model_kind="none",
            fallback_token="external_model:PPTC_2920L260",
            fallback="the model EV-014 found outside this library",
        )
        bad_token = await call_error(
            s, "set_spice_binding", part_id_or_mpn=pptc, model_kind="none",
            fallback_token="ask_someone",
        )
        bad_native = await call_error(
            s, "set_spice_binding", part_id_or_mpn=cap, model_kind="native_primitive",
            native={"letter": "Q"},
        )
        params_on_none = await call_error(
            s, "set_spice_binding", part_id_or_mpn=pptc, model_kind="none",
            params="r=1",
        )

    cb = c["spice_binding"]
    assert cb["device_type"] == "C" and cb["params"] == "c=1e-10"
    assert cb["native"]["parameters"] == {
        "value": 1e-10, "unit": "F", "source": "inventory snapshot",
    }
    assert cb["fallback_token"] == "native"
    assert c["warnings"] == []
    db_ = d["spice_binding"]
    assert db_["device_type"] == "D" and db_["params"] is None and db_["ports"] == ["A", "K"]
    assert db_["native"]["loses_behaviour"] is True
    assert d["warnings"] == ["native_loses_behaviour: partial: generic .model D (DC Vf roughly)"]
    assert n["spice_binding"]["fallback_token"] == "external_model:PPTC_2920L260"
    assert n["spice_binding"]["issues"] == ["no_model"]
    assert "eda.spice_fallback_token_unknown:" in bad_token
    assert "eda.spice_native_invalid:" in bad_native
    assert "eda.spice_not_applicable:" in params_on_none


# ---------------------------------------------------------------------
# Polarity, unexported library models, native_reason (ngSPICE 98edfea)
# ---------------------------------------------------------------------


async def test_polarised_part_on_symmetric_model_needs_positive_pin(authed_client, full_token):
    part_id = create_part(authed_client, "Electrolytic 100u")
    args = {"part_id_or_mpn": part_id, "pin_map": {"1": "p", "2": "n"}, "symbol_pins": ["1", "2"]}
    async with mcp_session(full_token) as s:
        await call(
            s, "upload_eda_asset", kind="spice", filename="capx.lib",
            content_base64=_b64(_fixture("cap_polarised_schema1.lib")), part_id=part_id,
        )
        first = await call(s, "set_spice_binding", **args)
        listed = await call(s, "list_spice_binding_issues")
        wrong_pin = await call_error(s, "set_spice_binding", **args, positive_pin="3")
        confirmed = await call(s, "set_spice_binding", **args, positive_pin="1")
        after = await call(s, "list_spice_binding_issues")

    binding = first["spice_binding"]
    assert binding["issues"] == ["polarity_needs_person"]
    assert binding["validated"] is False
    assert binding["polarity"]["part_polarized"] is True
    assert any(w.startswith("polarity_needs_person") for w in first["warnings"])
    assert first["sim_pins"] == "1=p 2=n"  # the pin order itself is written
    assert [(i["part_id"], i["issues"]) for i in listed["items"]] == [
        (part_id, ["polarity_needs_person"])
    ]
    assert "eda.spice_pin_unknown:" in wrong_pin
    assert confirmed["spice_binding"]["validated"] is True
    assert confirmed["spice_binding"]["positive_pin"] == "1"
    assert not any(w.startswith("polarity_needs_person") for w in confirmed["warnings"])
    assert after["items"] == []


async def test_library_model_without_loadable_export_is_recorded(authed_client, full_token):
    ldo = create_part(authed_client, "LDO whose model does not load")
    fuse = create_part(authed_client, "PPTC without a held value")
    reason = "does not load on any build: NG-012 (synthetic cause)"
    async with mcp_session(full_token) as s:
        unexported = await call(
            s, "set_spice_binding", part_id_or_mpn=ldo, model_kind="none", subckt="LDOX",
            export_reason=reason, fallback_token="attended_ltspice",
            fallback="model loads on no ngspice build",
        )
        no_value = await call(
            s, "set_spice_binding", part_id_or_mpn=fuse, model_kind="none",
            fallback_token="native", native_reason="no unambiguous value in the snapshot",
            native={
                "letter": "R", "parameters": None,
                "parameters_reason": "no unambiguous value in the snapshot",
                "loses_behaviour": True, "loses_behaviour_reason": "trip is thermal",
            },
        )
        positive_on_none = await call_error(
            s, "set_spice_binding", part_id_or_mpn=ldo, model_kind="none", positive_pin="1",
        )
        subckt_on_native = await call_error(
            s, "set_spice_binding", part_id_or_mpn=fuse, model_kind="native_primitive",
            device_type="R", params="r=1", subckt="X",
        )
        read = await call(s, "get_part_eda", part_id=ldo)

    b = unexported["spice_binding"]
    assert (b["model_kind"], b["subckt"], b["export_reason"]) == ("none", "LDOX", reason)
    assert b["fallback_token"] == "attended_ltspice" and b["issues"] == ["no_model"]
    assert read["spice_binding"]["export_reason"] == reason
    n = no_value["spice_binding"]
    assert n["native_reason"] == "no unambiguous value in the snapshot"
    assert n["native"]["letter"] == "R" and n["device_type"] is None and n["ports"] == []
    assert "eda.spice_not_applicable:" in positive_on_none
    assert "eda.spice_not_applicable:" in subckt_on_native


# ---------------------------------------------------------------------
# The key is always present; a file with nothing bound is an issue
# (production case: a part with a VDMOS model attached and no binding)
# ---------------------------------------------------------------------


async def test_spice_binding_key_is_always_present(authed_client, full_token):
    no_config = create_part(authed_client, "Never configured")
    footprint_only = create_part(authed_client, "Footprint only")
    r = authed_client.put(
        f"/api/parts/{footprint_only}/eda", json={"footprint_ref_external": "R:R_0402"}
    )
    assert r.status_code == 200, r.text
    unbound = create_part(authed_client, "P-MOSFET with model", mpn="SUD50P06-TEST")
    async with mcp_session(full_token) as s:
        uploaded = await call(
            s, "upload_eda_asset", kind="spice", filename="pmos_vdmos.lib",
            content_base64=_b64(b".model PMOSX VDMOS(pchan Vto=-2 Kp=5)\n.model DB D\n"),
            part_id=unbound,
        )
        a = await call(s, "get_part_eda", part_id=no_config)
        b = await call(s, "get_part_eda", part_id=footprint_only)
        c = await call(s, "get_part_eda", part_id="SUD50P06-TEST")
        bound = await _opa_part(s, authed_client)
        await _upload_opa_model(s, bound)
        await call(s, "set_spice_binding", part_id_or_mpn=bound, pin_map=OPA_PIN_MAP)
        d = await call(s, "get_part_eda", part_id=bound)

    no_model = {"source": "none", "state": "no_model", "validated": False}
    assert a["configured"] is False and a["spice_binding"] == no_model
    assert b["spice_binding"] == no_model
    # Two models in the file, so the upload recorded nothing: unbound.
    assert c["spice_binding"] == {
        "source": "none",
        "state": "unbound",
        "validated": False,
        "spice_datafile_id": uploaded["id"],
        "spice_name": "pmos_vdmos",
    }
    assert d["spice_binding"]["state"] == "bound"


async def test_attached_file_without_binding_is_listed_unbound(authed_client, full_token):
    part_id = create_part(authed_client, "P-MOSFET with model")
    free_text = create_part(authed_client, "Hand-written pins")
    async with mcp_session(full_token) as s:
        uploaded = await call(
            s, "upload_eda_asset", kind="spice", filename="pmos_vdmos.lib",
            content_base64=_b64(b".model PMOSX VDMOS(pchan Vto=-2 Kp=5)\n.model DB D\n"),
            part_id=part_id,
        )
        r = authed_client.put(
            f"/api/parts/{free_text}/eda",
            json={"spice_datafile_id": uploaded["id"], "sim_pins": "1=D 2=G 3=S"},
        )
        assert r.status_code == 200, r.text
        listed = await call(s, "list_spice_binding_issues")

    by_part = {i["part_id"]: i for i in listed["items"]}
    item = by_part[part_id]
    assert item["source"] == "none"
    assert item["issues"] == ["unbound"]
    assert item["spice_name"] == "pmos_vdmos"
    assert item["models"] == ["PMOSX", "DB"]
    assert "unbound" not in by_part[free_text]["issues"]
    assert by_part[free_text]["source"] == "free-text"


async def test_whitespace_sim_pins_and_dangling_file_do_not_break_the_listing(
    authed_client, full_token, db
):
    """Security review on #966: one bad row must not 500 the whole audit.

    A whitespace-only `sim_pins` used to be selected as free text by the
    SQL and handled as unbound by Python, which then read `.name` off a
    missing data file. A `spice_datafile_id` that resolves to nothing in
    this workspace (a foreign id written past the service) is the other
    half of that path.
    """
    blank_with_file = create_part(authed_client, "Blank pins, file attached")
    blank_dangling = create_part(authed_client, "Blank pins, dangling file")
    for part_id in (blank_with_file, blank_dangling):
        r = authed_client.put(f"/api/parts/{part_id}/eda", json={"sim_pins": " \t "})
        assert r.status_code == 200, r.text
    other = TestClient(app)
    signup_user(other)
    async with mcp_session(_mint(other)["token"]) as s:
        foreign = await call(
            s, "upload_eda_asset", kind="spice", filename="foreign.lib",
            content_base64=_b64(b".subckt FOREIGN a b\n.ends\n"),
        )
    async with mcp_session(full_token) as s:
        uploaded = await call(
            s, "upload_eda_asset", kind="spice", filename="mine.lib",
            content_base64=_b64(b".subckt MINE a b\n.ends\n"),
        )
        db.query(PartEda).filter_by(part_id=uuid.UUID(blank_with_file)).update(
            {"spice_datafile_id": uuid.UUID(uploaded["id"])}
        )
        db.query(PartEda).filter_by(part_id=uuid.UUID(blank_dangling)).update(
            {"spice_datafile_id": uuid.UUID(foreign["id"])}
        )
        db.commit()
        listed = await call(s, "list_spice_binding_issues")
        with_file = await call(s, "get_part_eda", part_id=blank_with_file)
        dangling = await call(s, "get_part_eda", part_id=blank_dangling)

    by_part = {i["part_id"]: i for i in listed["items"]}
    # Whitespace-only reads as empty everywhere: unbound, not free text.
    assert by_part[blank_with_file]["issues"] == ["unbound"]
    assert by_part[blank_with_file]["subckt"] == "MINE"
    # A foreign id resolves to nothing here: not listed (no file in this
    # workspace) and reported as no_model — and the foreign name never leaks.
    assert blank_dangling not in by_part
    assert with_file["spice_binding"]["state"] == "unbound"
    assert dangling["spice_binding"] == {
        "source": "none", "state": "no_model", "validated": False,
    }
    assert "FOREIGN" not in str(listed) and "foreign" not in str(dangling)


def test_unbound_item_tolerates_a_missing_datafile():
    from app.domain.eda.spice_binding_read import _unbound_item, sim_pins_blank

    item = _unbound_item({"part_id": "x"}, None, {})
    assert item["issues"] == ["no_model_attached"]
    assert "spice_name" not in item
    assert sim_pins_blank(None) and sim_pins_blank(" \t\r\n") and sim_pins_blank("")
    assert not sim_pins_blank(" 1=+ ")
