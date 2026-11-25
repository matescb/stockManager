"""Accepting pin/pad findings per part (SM-008b).

REST `GET /api/parts/{id}/eda/pin-pad`, `POST|DELETE …/pin-pad/accept`
and what MCP `check_pin_pad` shows of it. Pinned here:

* only an owner/admin browser SESSION accepts — every API token and
  lesser role gets `eda.review_requires_owner`;
* an acceptance is tied to both file hashes: a changed file shows it as
  stale and the finding counts again; changing back re-applies it;
* electrical findings need a note; unknown findings 404; a
  not-checkable part 409s;
* the verdict moves fail → pass_with_warnings → pass as findings are
  accepted, and every write is audited;
* workspace isolation, including two workspaces with byte-identical
  files (content hashes are not workspace-scoped).
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.domain.audit.models import AuditLog
from app.domain.parts.models import Part
from app.main import app
from tests._factories import create_part, signup_user
from tests.test_kicad_api import _token_client
from tests.test_mcp import _b64, _join_workspace, _mint, call, mcp_session

FIXTURES = Path(__file__).parent / "fixtures" / "eda" / "pin_pad"


@pytest.fixture
def full_token(authed_client) -> str:
    return _mint(authed_client)["token"]


async def _part(
    client: TestClient, token: str, symbol: str, footprint: str, name: str = "P"
) -> str:
    part_id = create_part(client, name)
    async with mcp_session(token) as s:
        for kind, filename in (("symbol", symbol), ("footprint", footprint)):
            await call(
                s,
                "upload_eda_asset",
                kind=kind,
                filename=filename,
                content_base64=_b64((FIXTURES / filename).read_bytes()),
                part_id=part_id,
            )
    return part_id


async def _repoint_footprint(token: str, part_id: str, content: bytes, name: str) -> None:
    async with mcp_session(token) as s:
        uploaded = await call(
            s, "upload_eda_asset", kind="footprint", filename=name,
            content_base64=_b64(content),
        )
        config = await call(s, "get_part_eda", part_id=part_id)
        await call(
            s, "set_part_eda", part_id=part_id,
            symbol_id=config["symbol_id"], footprint_id=uploaded["id"],
        )


def _accept(client: TestClient, part_id: str, kind: str, number: str, note=None):
    body = {"finding_kind": kind, "number": number}
    if note is not None:
        body["note"] = note
    return client.post(f"/api/parts/{part_id}/eda/pin-pad/accept", json=body)


def _revoke(client: TestClient, part_id: str, kind: str, number: str):
    return client.delete(
        f"/api/parts/{part_id}/eda/pin-pad/accept",
        params={"finding_kind": kind, "number": number},
    )


def _get(client: TestClient, part_id: str) -> dict:
    r = client.get(f"/api/parts/{part_id}/eda/pin-pad")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _classified(check: dict) -> dict[str, dict]:
    return {f["number"]: f for f in check["findings"]["classified"]}


async def test_accepting_warnings_moves_the_verdict_to_pass(authed_client, full_token, db):
    part = await _part(
        authed_client, full_token, "qfn32_usb_uart.kicad_sym", "qfn32_ep33.kicad_mod"
    )
    before = _get(authed_client, part)
    assert before["verdict"] == "pass_with_warnings"
    assert before["can_accept"] is True
    assert (before["warnings_count"], before["electrical_count"]) == (7, 0)

    r = _accept(authed_client, part, "pad_without_pin", "33")
    assert r.status_code == 200, r.text
    after_one = r.json()["data"]
    assert after_one["warnings_count"] == 6 and after_one["accepted_count"] == 1
    accepted = _classified(after_one)["33"]["accepted"]
    assert accepted["note"] is None and accepted["by_name"] and accepted["at"]

    for number in ("5", "12", "13", "23", "25", "29"):
        assert _accept(authed_client, part, "pad_without_pin", number).status_code == 200
    final = _get(authed_client, part)
    assert final["verdict"] == "pass"
    assert final["accepted_count"] == 7 and final["warnings_count"] == 0

    async with mcp_session(full_token) as s:
        seen = await call(s, "check_pin_pad", part_id_or_mpn=part)
    assert seen["verdict"] == "pass"
    assert _classified(seen)["33"]["accepted"]["by"] == accepted["by"]
    actions = db.execute(
        select(AuditLog.action, AuditLog.comment).where(
            AuditLog.action == "eda_pin_pad.accepted"
        )
    ).all()
    assert len(actions) == 7
    assert all("class=" in c and "footprint_sha256=" in c for _, c in actions)


async def test_electrical_finding_needs_a_note(authed_client, full_token):
    part = await _part(authed_client, full_token, "soic8.kicad_sym", "soic8_pad9.kicad_mod")
    assert _get(authed_client, part)["verdict"] == "fail"

    bare = _accept(authed_client, part, "pad_without_pin", "9")
    blank = _accept(authed_client, part, "pad_without_pin", "9", note="   ")
    noted = _accept(authed_client, part, "pad_without_pin", "9", note="pad 9 is a test point")

    assert bare.status_code == 422 and bare.json()["code"] == "eda.pin_pad_note_required"
    assert blank.status_code == 422
    assert noted.status_code == 200, noted.text
    data = noted.json()["data"]
    assert data["verdict"] == "pass"
    assert _classified(data)["9"]["accepted"]["note"] == "pad 9 is a test point"


async def test_acceptance_goes_stale_when_a_file_changes(authed_client, full_token):
    part = await _part(authed_client, full_token, "soic8.kicad_sym", "soic8_pad9.kicad_mod")
    _accept(authed_client, part, "pad_without_pin", "9", note="checked")
    assert _get(authed_client, part)["verdict"] == "pass"

    original = (FIXTURES / "soic8_pad9.kicad_mod").read_bytes()
    edited = original.replace(b'(footprint "SOIC8_PAD9"', b'(footprint "SOIC8_PAD9_B"')
    await _repoint_footprint(full_token, part, edited, "soic8_pad9_b.kicad_mod")
    changed = _get(authed_client, part)
    assert changed["verdict"] == "fail"
    assert "accepted" not in _classified(changed)["9"]
    assert [(a["finding_kind"], a["number"], a["note"]) for a in changed["stale_acceptances"]] == [
        ("pad_without_pin", "9", "checked")
    ]
    assert changed["stale_acceptances"][0]["footprint_sha256"] != changed["footprint_sha256"]

    # Back on the accepted bytes, the acceptance applies again.
    await _repoint_footprint(full_token, part, original, "soic8_pad9.kicad_mod")
    restored = _get(authed_client, part)
    assert restored["verdict"] == "pass" and restored["stale_acceptances"] == []

    # Revoking clears every acceptance of that finding.
    r = _revoke(authed_client, part, "pad_without_pin", "9")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["verdict"] == "fail"
    again = _revoke(authed_client, part, "pad_without_pin", "9")
    assert again.status_code == 404
    assert again.json()["code"] == "eda.pin_pad_acceptance_not_found"


async def test_re_accepting_updates_the_one_row(authed_client, full_token, db):
    part = await _part(authed_client, full_token, "sot23_5_ldo.kicad_sym", "sot23_5.kicad_mod")
    _accept(authed_client, part, "pad_without_pin", "4", note="first")
    second = _accept(authed_client, part, "pad_without_pin", "4", note="second")
    assert _classified(second.json()["data"])["4"]["accepted"]["note"] == "second"
    count = db.execute(
        text("SELECT count(*) FROM pin_pad_acceptances WHERE part_id = :p"),
        {"p": part},
    ).scalar_one()
    assert count == 1


async def test_unknown_finding_and_not_checkable_part(authed_client, full_token):
    part = await _part(authed_client, full_token, "soic8.kicad_sym", "soic8_mech.kicad_mod")
    bare = create_part(authed_client, "No CAD")

    unknown = _accept(authed_client, part, "pad_without_pin", "9")
    wrong_kind = _accept(authed_client, part, "bogus", "9")
    blocked = _accept(authed_client, bare, "pad_without_pin", "1")
    extra = authed_client.post(
        f"/api/parts/{part}/eda/pin-pad/accept",
        json={"finding_kind": "pad_without_pin", "number": "9", "by": "someone"},
    )

    assert unknown.status_code == 404
    assert unknown.json()["code"] == "eda.pin_pad_finding_not_found"
    assert wrong_kind.status_code == 422
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "eda.pin_pad_not_checkable"
    assert _get(authed_client, bare)["verdict"] == "not-checkable"
    assert extra.status_code == 422


async def test_every_api_token_is_refused(authed_client, full_token):
    """Even the OWNER's own full-access token cannot accept or revoke."""
    part = await _part(authed_client, full_token, "sot23_5_ldo.kicad_sym", "sot23_5.kicad_mod")
    token_client = _token_client(full_token)
    bearer = TestClient(app)
    bearer.headers["Authorization"] = f"Bearer {full_token}"

    for client in (token_client, bearer):
        accept = _accept(client, part, "pad_without_pin", "4")
        revoke = _revoke(client, part, "pad_without_pin", "4")
        listing = client.get(f"/api/parts/{part}/eda/pin-pad")
        assert accept.status_code == 403
        assert accept.json()["code"] == "eda.review_requires_owner"
        assert revoke.status_code == 403
        assert listing.status_code == 200
        assert listing.json()["data"]["can_accept"] is False
    assert _get(authed_client, part)["accepted_count"] == 0


@pytest.mark.parametrize("role", ["member", "viewer"])
async def test_lesser_roles_cannot_accept(authed_client, full_token, role):
    part = await _part(authed_client, full_token, "sot23_5_ldo.kicad_sym", "sot23_5.kicad_mod")
    joiner = _join_workspace(authed_client, role)

    r = _accept(joiner, part, "pad_without_pin", "4")
    assert r.status_code == 403
    assert r.json()["code"] in {
        "eda.review_requires_owner",
        "resource.insufficient_role",  # viewer: the router's member gate fires first
    }
    assert _get(joiner, part)["can_accept"] is False


async def test_admin_session_can_accept(authed_client, full_token):
    part = await _part(authed_client, full_token, "sot23_5_ldo.kicad_sym", "sot23_5.kicad_mod")
    admin = _join_workspace(authed_client, "admin")
    r = _accept(admin, part, "pad_without_pin", "4")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["verdict"] == "pass"


async def test_workspace_isolation_even_with_identical_files(authed_client, full_token):
    mine = await _part(authed_client, full_token, "soic8.kicad_sym", "soic8_pad9.kicad_mod")
    other = TestClient(app)
    signup_user(other)
    theirs = await _part(
        other, _mint(other)["token"], "soic8.kicad_sym", "soic8_pad9.kicad_mod"
    )
    assert _accept(authed_client, mine, "pad_without_pin", "9", note="ok").status_code == 200

    # Same bytes, same hashes, other workspace: nothing applies or shows.
    their_check = _get(other, theirs)
    assert their_check["verdict"] == "fail"
    assert their_check["stale_acceptances"] == []
    assert their_check["symbol_sha256"] == _get(authed_client, mine)["symbol_sha256"]

    # And neither can reach the other's part.
    for client, part in ((other, mine), (authed_client, theirs)):
        assert client.get(f"/api/parts/{part}/eda/pin-pad").status_code == 404
        r = _accept(client, part, "pad_without_pin", "9", note="x")
        assert r.status_code == 404 and r.json()["code"] == "part.not_found"
        assert _revoke(client, part, "pad_without_pin", "9").status_code == 404


def test_trigger_refuses_a_cross_workspace_row(db, authed_client):
    part = create_part(authed_client, "Trigger target")
    other = TestClient(app)
    signup_user(other)
    foreign_ws = db.get(Part, uuid.UUID(create_part(other, "Theirs"))).workspace_id
    with pytest.raises(DBAPIError) as caught:
        db.execute(
            text(
                "INSERT INTO pin_pad_acceptances (id, workspace_id, part_id, "
                "symbol_sha256, footprint_sha256, finding_kind, number, "
                "finding_class, accepted_at) VALUES (:id, :ws, :part, 'a', 'b', "
                "'pad_without_pin', '9', 'electrical', now())"
            ),
            {"id": uuid.uuid4(), "ws": foreign_ws, "part": part},
        )
    assert "WS001" in str(caught.value) or "not in workspace" in str(caught.value)


async def test_acceptance_of_a_warning_never_excuses_it_once_electrical(
    authed_client, full_token
):
    """Same bytes, new class: the part leaves its connector category, so
    the shell pads read `electrical` and a note-free acceptance given to
    a `shell` must not keep excusing them."""
    part = await _part(authed_client, full_token, "rf_coax_1.kicad_sym", "rf_5pad_plain.kicad_mod")
    async with mcp_session(full_token) as s:
        await call(s, "create_category", name="RF Connectors")
        await call(s, "create_category", name="Misc")
        await call(s, "set_part_category", part_id_or_mpn=part, category_id_or_name="RF Connectors")
    filed = _get(authed_client, part)
    assert filed["verdict"] == "pass_with_warnings"
    assert _classified(filed)["2"]["class"] == "shell"
    assert _accept(authed_client, part, "pad_without_pin", "2").status_code == 200

    async with mcp_session(full_token) as s:
        await call(s, "set_part_category", part_id_or_mpn=part, category_id_or_name="Misc")
    moved = _get(authed_client, part)
    assert moved["verdict"] == "fail"
    assert _classified(moved)["2"]["class"] == "electrical"
    assert "accepted" not in _classified(moved)["2"]
    assert [a["number"] for a in moved["stale_acceptances"]] == ["2"]


async def test_revoke_with_hashes_clears_only_that_stale_row(authed_client, full_token):
    part = await _part(authed_client, full_token, "soic8.kicad_sym", "soic8_pad9.kicad_mod")
    _accept(authed_client, part, "pad_without_pin", "9", note="old files")
    original = (FIXTURES / "soic8_pad9.kicad_mod").read_bytes()
    edited = original.replace(b'(footprint "SOIC8_PAD9"', b'(footprint "SOIC8_PAD9_C"')
    await _repoint_footprint(full_token, part, edited, "soic8_pad9_c.kicad_mod")
    _accept(authed_client, part, "pad_without_pin", "9", note="new files")
    stale = _get(authed_client, part)["stale_acceptances"][0]

    half = authed_client.delete(
        f"/api/parts/{part}/eda/pin-pad/accept",
        params={"finding_kind": "pad_without_pin", "number": "9",
                "symbol_sha256": stale["symbol_sha256"]},
    )
    scoped = authed_client.delete(
        f"/api/parts/{part}/eda/pin-pad/accept",
        params={"finding_kind": "pad_without_pin", "number": "9",
                "symbol_sha256": stale["symbol_sha256"],
                "footprint_sha256": stale["footprint_sha256"]},
    )

    assert half.status_code == 422 and half.json()["code"] == "eda.pin_pad_hash_pair"
    assert scoped.status_code == 200, scoped.text
    after = scoped.json()["data"]
    assert after["stale_acceptances"] == []
    assert after["verdict"] == "pass"
    assert _classified(after)["9"]["accepted"]["note"] == "new files"


async def test_acceptance_applies_only_while_the_class_is_unchanged(
    authed_client, full_token
):
    """Strict class match, the other direction too: an electrical finding
    accepted with a note, which later reads `shell`, is not silently
    carried over — the acceptance is stale and the finding counts again
    (as a warning now)."""
    part = await _part(authed_client, full_token, "rf_coax_1.kicad_sym", "rf_5pad_plain.kicad_mod")
    async with mcp_session(full_token) as s:
        await call(s, "create_category", name="Coax Connectors")
    plain = _get(authed_client, part)
    assert _classified(plain)["3"]["class"] == "electrical"
    assert _accept(authed_client, part, "pad_without_pin", "3", note="GND").status_code == 200
    assert "accepted" in _classified(_get(authed_client, part))["3"]

    async with mcp_session(full_token) as s:
        await call(
            s, "set_part_category", part_id_or_mpn=part, category_id_or_name="Coax Connectors"
        )
    moved = _get(authed_client, part)
    assert _classified(moved)["3"]["class"] == "shell"
    assert "accepted" not in _classified(moved)["3"]
    assert [(a["number"], a["class"]) for a in moved["stale_acceptances"]] == [
        ("3", "electrical")
    ]
    assert moved["accepted_count"] == 0 and moved["warnings_count"] == 4
