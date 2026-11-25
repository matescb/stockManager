"""`check_pin_pad`, `list_pin_pad_issues` and `get_part_eda.pin_pad_check` (SM-008).

Driven through the real MCP client. The rules themselves are pinned on
fixtures in `test_eda_pin_pad.py`; this file pins what the tools add:
which files a part resolves to, the hashes the verdict carries, that a
changed file changes the verdict (never stale), workspace isolation,
read-only-ness and the cursor walk.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app.domain.eda import storage
from app.domain.parts.models import Part
from app.main import app
from tests._factories import create_part, signup_user
from tests.test_mcp import _b64, _mint, _zip_bytes, call, call_error, mcp_session

FIXTURES = Path(__file__).parent / "fixtures" / "eda" / "pin_pad"


async def _attach(s, part_id: str, symbol: str | None, footprint: str | None) -> dict:
    """Upload fixtures and wire them into the part's empty slots."""
    out = {}
    for kind, name in (("symbol", symbol), ("footprint", footprint)):
        if name is None:
            continue
        out[kind] = await call(
            s,
            "upload_eda_asset",
            kind=kind,
            filename=name,
            content_base64=_b64((FIXTURES / name).read_bytes()),
            part_id=part_id,
        )
    return out


async def test_pass_and_fail_with_hashes_refs_and_get_part_eda(authed_client):
    """Acceptance: pass for 1-8 + mechanical, fail naming pad 9, hashes,
    and `get_part_eda` shows the same verdict."""
    good = create_part(authed_client, "Good SOIC", mpn="PP-GOOD")
    bad = create_part(authed_client, "Bad SOIC", mpn="PP-BAD")
    token = _mint(authed_client)["token"]
    async with mcp_session(token) as s:
        good_files = await _attach(s, good, "soic8.kicad_sym", "soic8_mech.kicad_mod")
        bad_files = await _attach(s, bad, "soic8.kicad_sym", "soic8_pad9.kicad_mod")
        before = await call(s, "get_part_eda", part_id=good)
        passed = await call(s, "check_pin_pad", part_id_or_mpn=good)
        failed = await call(s, "check_pin_pad", part_id_or_mpn="PP-BAD")

    assert passed["verdict"] == "pass"
    assert passed["part_id"] == good
    assert passed["symbol_sha256"] == good_files["symbol"]["sha256"]
    assert passed["footprint_sha256"] == good_files["footprint"]["sha256"]
    assert passed["symbol_ref"].endswith(":SOIC8_DEVICE")
    assert passed["footprint_ref"].endswith(":SOIC8_MECH")
    assert passed["checked_at"] and "reason" not in passed

    assert failed["verdict"] == "fail"
    assert failed["findings"]["pads_without_pin"] == ["9"]
    assert failed["footprint_sha256"] == bad_files["footprint"]["sha256"]

    check = before["pin_pad_check"]
    assert check["verdict"] == "pass"
    assert check["symbol_sha256"] == passed["symbol_sha256"]
    # Additive: the existing keys are still there.
    assert {"configured", "symbol_id", "footprint_id", "symbol_ref"} <= set(before)


async def test_verdict_follows_the_current_files(authed_client):
    """The interface's `stale`: the verdict is recomputed, so re-pointing
    the footprint changes both the verdict and the hash it carries."""
    part = create_part(authed_client, "Repointed", mpn="PP-REPOINT")
    token = _mint(authed_client)["token"]
    async with mcp_session(token) as s:
        await _attach(s, part, "soic8.kicad_sym", "soic8_pad9.kicad_mod")
        first = await call(s, "check_pin_pad", part_id_or_mpn=part)
        mech = await call(
            s,
            "upload_eda_asset",
            kind="footprint",
            filename="soic8_mech.kicad_mod",
            content_base64=_b64((FIXTURES / "soic8_mech.kicad_mod").read_bytes()),
        )
        config = await call(s, "get_part_eda", part_id=part)
        await call(
            s,
            "set_part_eda",
            part_id=part,
            symbol_id=config["symbol_id"],
            footprint_id=mech["id"],
        )
        second = await call(s, "check_pin_pad", part_id_or_mpn=part)
    assert first["verdict"] == "fail"
    assert second["verdict"] == "pass"
    assert second["footprint_sha256"] == mech["sha256"] != first["footprint_sha256"]


async def test_not_checkable_reasons(authed_client, db):
    external = create_part(authed_client, "Local lib")
    no_fp = create_part(authed_client, "Symbol only")
    unconfigured = create_part(authed_client, "Nothing")
    gone = create_part(authed_client, "File gone")
    token = _mint(authed_client)["token"]
    async with mcp_session(token) as s:
        await call(
            s,
            "set_part_eda",
            part_id=external,
            symbol_ref_external="Device:R",
            footprint_ref_external="Resistor_SMD:R_0603_1608Metric",
        )
        await _attach(s, no_fp, "nc_pin.kicad_sym", None)
        files = await _attach(s, gone, "dual_opamp.kicad_sym", "soic8_no_pad5.kicad_mod")
        ws_id = db.get(Part, uuid.UUID(gone)).workspace_id
        os.unlink(storage.path_for(ws_id, f"{files['symbol']['sha256']}.kicad_sym"))
        results = {
            pid: await call(s, "check_pin_pad", part_id_or_mpn=pid)
            for pid in (external, no_fp, unconfigured, gone)
        }
        nothing = await call(s, "get_part_eda", part_id=unconfigured)
    assert results[external]["reason"] == "symbol_external"
    assert results[external]["symbol_ref"] == "Device:R"
    assert results[external]["symbol_sha256"] is None
    assert results[no_fp]["reason"] == "footprint_missing"
    assert results[unconfigured]["reason"] == "symbol_missing"
    assert results[gone]["reason"] == "symbol_unreadable"
    assert all(r["verdict"] == "not-checkable" for r in results.values())
    assert nothing["configured"] is False
    assert nothing["pin_pad_check"]["reason"] == "symbol_missing"


async def test_vendor_zip_pair_with_exposed_pad_passes(authed_client):
    """A SnapEDA-layout zip whose footprint has an EP, thermal vias and
    paste apertures sharing pin 9's number."""
    part = create_part(authed_client, "QFN with EP", mpn="PP-QFN")
    archive = _zip_bytes(
        {
            "QFN8_EP.kicad_sym": (FIXTURES / "qfn8_ep.kicad_sym").read_bytes(),
            "QFN8_EP_VIAS.kicad_mod": (FIXTURES / "qfn8_ep_vias.kicad_mod").read_bytes(),
        }
    )
    token = _mint(authed_client)["token"]
    async with mcp_session(token) as s:
        imported = await call(
            s, "import_vendor_zip", part_id=part, content_base64=_b64(archive)
        )
        result = await call(s, "check_pin_pad", part_id_or_mpn="PP-QFN")
    assert imported["part_eda_updated"] is True
    assert result["verdict"] == "pass", result


async def _walk(s, **kwargs) -> list[dict]:
    items, cursor = [], None
    for _ in range(50):
        args = dict(kwargs)
        if cursor:
            args["cursor"] = cursor
        page = await call(s, "list_pin_pad_issues", **args)
        items += page["items"]
        cursor = page["next_cursor"]
        if cursor is None:
            return items
    raise AssertionError("cursor never terminated")


async def test_listing_filters_pages_and_isolates(authed_client):
    fail_a = create_part(authed_client, "Fail A")
    fail_b = create_part(authed_client, "Fail B")
    passing = create_part(authed_client, "Pass")
    external = create_part(authed_client, "External")
    create_part(authed_client, "No CAD at all")
    other = TestClient(app)
    signup_user(other)
    theirs = create_part(other, "Their fail", mpn="PP-THEIRS")

    async with mcp_session(_mint(other)["token"]) as s:
        await _attach(s, theirs, "soic8.kicad_sym", "soic8_pad9.kicad_mod")
    async with mcp_session(_mint(authed_client)["token"]) as s:
        await _attach(s, fail_a, "soic8.kicad_sym", "soic8_pad9.kicad_mod")
        await _attach(s, fail_b, "soic8.kicad_sym", "soic8_pad9.kicad_mod")
        await _attach(s, passing, "qfn8_ep.kicad_sym", "qfn8_ep_vias.kicad_mod")
        await call(s, "set_part_eda", part_id=external, symbol_ref_external="Device:R")

    async with mcp_session(_mint(authed_client, read_only=True)["token"]) as s:
        everything = await _walk(s, page_size=1)
        fails = await _walk(s, verdict="fail")
        blocked = await _walk(s, verdict="not-checkable")
        foreign = await call_error(s, "check_pin_pad", part_id_or_mpn=theirs)
        foreign_mpn = await call_error(s, "check_pin_pad", part_id_or_mpn="PP-THEIRS")
        bad_cursor = await call_error(s, "list_pin_pad_issues", cursor="nope")
        export = await call(s, "export_parts", page_size=1)
        borrowed = await call_error(
            s, "list_pin_pad_issues", cursor=export["next_cursor"]
        )
        clamped = await call(s, "list_pin_pad_issues", page_size=10_000)

    ids = [item["part_id"] for item in everything]
    assert sorted(ids) == sorted([fail_a, fail_b, external]), ids
    assert len(ids) == len(set(ids))
    assert {i["part_id"] for i in fails} == {fail_a, fail_b}
    assert all(i["findings"]["pads_without_pin"] == ["9"] for i in fails)
    assert [i["part_id"] for i in blocked] == [external]
    assert blocked[0]["reason"] == "symbol_external"
    assert theirs not in ids
    assert "part.not_found" in foreign and "part.not_found" in foreign_mpn
    assert "mcp.invalid_cursor" in bad_cursor
    assert "mcp.invalid_cursor" in borrowed
    assert clamped["page_size"] == 100


async def test_warnings_verdict_is_listed_and_classified(authed_client):
    """SM-008b: a part with only non-electrical findings is
    `pass_with_warnings`, listed under `warnings` and `all`, never `fail`;
    the payload carries the classified findings and the counts."""
    warn = create_part(authed_client, "FT232-like", mpn="PP-WARN")
    fail = create_part(authed_client, "Pad 9")
    async with mcp_session(_mint(authed_client)["token"]) as s:
        await _attach(s, warn, "qfn32_usb_uart.kicad_sym", "qfn32_ep33.kicad_mod")
        await _attach(s, fail, "soic8.kicad_sym", "soic8_pad9.kicad_mod")
        checked = await call(s, "check_pin_pad", part_id_or_mpn="PP-WARN")
        warnings = await _walk(s, verdict="warnings")
        fails = await _walk(s, verdict="fail")
        everything = await _walk(s)
        eda = await call(s, "get_part_eda", part_id=warn)

    assert checked["verdict"] == "pass_with_warnings"
    assert (checked["warnings_count"], checked["electrical_count"]) == (7, 0)
    assert checked["accepted_count"] == 0 and checked["stale_acceptances"] == []
    classes = {f["number"]: f["class"] for f in checked["findings"]["classified"]}
    assert classes["33"] == "exposed_pad" and classes["5"] == "nc_pin"
    assert [i["part_id"] for i in warnings] == [warn]
    assert [i["part_id"] for i in fails] == [fail]
    assert {i["part_id"] for i in everything} == {warn, fail}
    assert eda["pin_pad_check"]["verdict"] == "pass_with_warnings"
