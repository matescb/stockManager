"""SM-009 over REST: `GET/PUT /api/parts/{id}/eda` carry `spice_binding`.

netsch reads the REST surface as well as MCP, so the additive object must
be the same one MCP `get_part_eda` reports. Existing keys are unchanged,
and a part with no configuration still answers `data: null`.
"""
from __future__ import annotations

from tests._factories import create_part
from tests.test_mcp import _mint, call, mcp_session

_EXISTING_KEYS = {
    "part_id", "symbol_id", "symbol_ref_external", "footprint_id",
    "footprint_ref_external", "spice_datafile_id", "value", "keywords",
    "footprint_filters", "exclude_from_bom", "exclude_from_board",
    "exclude_from_sim", "sim_device", "sim_pins", "sim_params",
}


def _upload_spice(client, name: str, body: bytes) -> dict:
    r = client.post(
        "/api/eda/datafiles",
        files={"file": (f"{name}.lib", body, "application/octet-stream")},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]


async def test_rest_part_eda_reports_unbound_then_bound(authed_client):
    part_id = create_part(authed_client, "Divider", mpn="REST-SPICE-1")
    unconfigured = create_part(authed_client, "Nothing yet")
    datafile = _upload_spice(authed_client, "divider", b".subckt DIV a b\nR1 a b 1k\n.ends\n")

    put = authed_client.put(
        f"/api/parts/{part_id}/eda",
        json={"symbol_ref_external": "Device:R", "spice_datafile_id": datafile["id"]},
    )
    assert put.status_code == 200, put.text
    got = authed_client.get(f"/api/parts/{part_id}/eda").json()["data"]

    unbound = {
        "source": "none",
        "state": "unbound",
        "validated": False,
        "spice_datafile_id": datafile["id"],
        "spice_name": datafile["name"],
    }
    assert put.json()["data"]["spice_binding"] == unbound
    assert got["spice_binding"] == unbound
    assert set(got) == _EXISTING_KEYS | {"spice_binding"}
    assert authed_client.get(f"/api/parts/{unconfigured}/eda").json()["data"] is None

    async with mcp_session(_mint(authed_client)["token"]) as s:
        await call(
            s, "set_spice_binding", part_id_or_mpn="REST-SPICE-1",
            pin_map={"1": "a", "2": "b"}, symbol_pins=["1", "2"],
        )
        mcp_view = await call(s, "get_part_eda", part_id=part_id)
    bound = authed_client.get(f"/api/parts/{part_id}/eda").json()["data"]

    assert bound["spice_binding"]["state"] == "bound"
    assert bound["spice_binding"]["validated"] is True
    assert bound["spice_binding"]["ports"] == ["a", "b"]
    assert bound["sim_pins"] == "1=a 2=b"
    # Same object on both surfaces.
    assert bound["spice_binding"] == mcp_view["spice_binding"]


def test_rest_no_model_and_free_text_states(authed_client):
    plain = create_part(authed_client, "Footprint only")
    free = create_part(authed_client, "Hand-written pins")
    authed_client.put(f"/api/parts/{plain}/eda", json={"footprint_ref_external": "R:R_0402"})
    authed_client.put(f"/api/parts/{free}/eda", json={"sim_pins": "1=+ 2=-"})

    assert authed_client.get(f"/api/parts/{plain}/eda").json()["data"]["spice_binding"] == {
        "source": "none", "state": "no_model", "validated": False,
    }
    assert authed_client.get(f"/api/parts/{free}/eda").json()["data"]["spice_binding"] == {
        "source": "free-text", "state": "free_text", "validated": False, "sim_pins": "1=+ 2=-",
    }


