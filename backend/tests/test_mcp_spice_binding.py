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
    assert "spice_binding" not in before
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
