"""SM-010: per-version metadata and review state on a part's CAD files.

Acceptance map (edaflow modules/stockmanager.md SM-010, plus the brief):
* an asset carries origin, model_kind, simulators, valid_for,
  not_valid_for, review {state, by, at, against}; get_part_eda returns them
  → test_metadata_round_trips_through_get_part_eda,
    test_owner_session_records_a_review_visible_over_mcp
* a new file version (new sha256) starts unreviewed
  → test_a_new_file_version_starts_unreviewed
* an API token cannot change review (MCP and REST)
  → test_review_arguments_over_mcp_are_refused_and_write_nothing,
    test_rest_review_refuses_every_api_token
* only owner/admin sessions review → test_member_session_cannot_review
* workspace isolation → test_other_workspace_cannot_read_or_write
* read-only token refused for the write tool
  → test_read_only_token_is_refused_for_set_asset_metadata
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domain.audit.models import AuditLog
from app.domain.eda.models import EdaAssetMetadata
from app.main import app
from tests._factories import create_part, signup_user
from tests.test_kicad_api import _token_client
from tests.test_mcp import (
    _b64,
    _join_workspace,
    _mint,
    _snapeda_zip,
    call,
    call_error,
    mcp_session,
)

SPICE = Path(__file__).parent / "fixtures" / "spice"
SYMBOL = (SPICE / "opa192.kicad_sym").read_bytes()


@pytest.fixture
def full_token(authed_client) -> str:
    return _mint(authed_client)["token"]


async def _part_with_symbol_and_model(s, client: TestClient, mpn: str = "SM010-OPA") -> str:
    part_id = create_part(client, "SM-010 op-amp", mpn=mpn)
    await call(
        s, "upload_eda_asset", kind="symbol", filename="OPA192.kicad_sym",
        content_base64=_b64(SYMBOL), part_id=part_id,
    )
    await call(
        s, "upload_eda_asset", kind="spice", filename="OPA192.lib",
        content_base64=_b64((SPICE / "opa192_continuation.lib").read_bytes()),
        part_id=part_id,
    )
    return part_id


def _review(client: TestClient, part_id: str, kind: str, **body):
    return client.put(f"/api/parts/{part_id}/eda/{kind}/review", json=body)


# ---------------------------------------------------------------------
# Descriptive metadata (MCP)
# ---------------------------------------------------------------------


async def test_metadata_round_trips_through_get_part_eda(authed_client, full_token, db):
    async with mcp_session(full_token) as s:
        part_id = await _part_with_symbol_and_model(s, authed_client)
        before = await call(s, "get_part_eda", part_id=part_id)
        result = await call(
            s, "set_asset_metadata", part_id_or_mpn="SM010-OPA", kind="spice",
            origin="https://www.ti.com/lit/zip/sbom123", model_kind="vendor",
            simulators=["ngspice-43", "ngspice-43", " LTspice 24 "],
            valid_for=["dc", "ac", "transient"], not_valid_for=["noise"],
            note="vendor PSpice model, unmodified",
        )
        eda = await call(s, "get_part_eda", part_id=part_id)

    # Before any write: the hosted files are listed, unreviewed, and the
    # server-set `source` is reported untouched.
    assert before["assets"]["symbol"]["review"]["state"] == "unreviewed"
    assert before["assets"]["spice"]["source"] == "manual"
    assert set(before["assets"]) == {"symbol", "spice"}

    spice = eda["assets"]["spice"]
    assert result["asset"] == spice
    assert result["updated"] == [
        "model_kind", "not_valid_for", "note", "origin", "simulators", "valid_for"
    ]
    assert spice["origin"] == "https://www.ti.com/lit/zip/sbom123"
    assert spice["origin_from"] == "metadata"
    assert spice["model_kind"] == "vendor"
    assert spice["simulators"] == ["ngspice-43", "LTspice 24"]
    assert spice["valid_for"] == ["dc", "ac", "transient"]
    assert spice["not_valid_for"] == ["noise"]
    assert spice["note"] == "vendor PSpice model, unmodified"
    assert spice["review"] == {
        "state": "unreviewed", "by": None, "by_name": None, "at": None, "against": None
    }
    assert spice["source"] == "manual"
    # SM-009's key is still there beside it.
    assert eda["spice_binding"]["subckt"] == "OPA192"

    rows = db.query(AuditLog).filter(AuditLog.action == "eda_asset_metadata.set").all()
    assert len(rows) == 1
    assert part_id in [str(t) for t in rows[0].target_ids]
    assert rows[0].comment.startswith(f"kind=spice,sha256={spice['sha256']},fields=")
    assert "ti.com" not in rows[0].comment


async def test_set_asset_metadata_merges_and_clears(authed_client, full_token):
    async with mcp_session(full_token) as s:
        part_id = await _part_with_symbol_and_model(s, authed_client)
        await call(
            s, "set_asset_metadata", part_id_or_mpn=part_id, kind="symbol",
            origin="SnapMagic export", simulators=["ngspice-43"],
        )
        await call(s, "set_asset_metadata", part_id_or_mpn=part_id, kind="symbol", note="x")
        kept = (await call(s, "get_part_eda", part_id=part_id))["assets"]["symbol"]
        await call(
            s, "set_asset_metadata", part_id_or_mpn=part_id, kind="symbol",
            origin="", simulators=[],
        )
        cleared = (await call(s, "get_part_eda", part_id=part_id))["assets"]["symbol"]

    assert kept["origin"] == "SnapMagic export"
    assert kept["simulators"] == ["ngspice-43"]
    assert kept["note"] == "x"
    assert cleared["origin"] is None
    assert cleared["simulators"] == []
    assert cleared["note"] == "x"


async def test_invalid_metadata_is_refused(authed_client, full_token, db):
    async with mcp_session(full_token) as s:
        part_id = await _part_with_symbol_and_model(s, authed_client)
        on_symbol = await call_error(
            s, "set_asset_metadata", part_id_or_mpn=part_id, kind="symbol",
            model_kind="fitted",
        )
        nothing = await call_error(
            s, "set_asset_metadata", part_id_or_mpn=part_id, kind="spice"
        )
        too_many = await call_error(
            s, "set_asset_metadata", part_id_or_mpn=part_id, kind="spice",
            valid_for=[f"a{i}" for i in range(21)],
        )
        no_footprint = await call_error(
            s, "set_asset_metadata", part_id_or_mpn=part_id, kind="footprint",
            origin="x",
        )
        bare = create_part(authed_client, "no cad")
        unconfigured = await call_error(
            s, "set_asset_metadata", part_id_or_mpn=bare, kind="symbol", origin="x"
        )

    for message in (on_symbol, nothing, too_many):
        assert "eda.asset_metadata_invalid" in message, message
    assert "eda.asset_not_attached" in no_footprint, no_footprint
    assert "eda.asset_not_attached" in unconfigured, unconfigured
    assert db.query(EdaAssetMetadata).count() == 0


async def test_model3d_is_the_footprints_first_model(authed_client, full_token):
    async with mcp_session(full_token) as s:
        part_id = create_part(authed_client, "zip part", mpn="SM010-ZIP")
        await call(
            s, "import_vendor_zip", part_id=part_id,
            content_base64=_b64(_snapeda_zip("SM010ZIP")),
        )
        await call(
            s, "set_asset_metadata", part_id_or_mpn=part_id, kind="model3d",
            origin="SnapMagic zip",
        )
        assets = (await call(s, "get_part_eda", part_id=part_id))["assets"]

    assert set(assets) == {"symbol", "footprint", "model3d"}
    assert assets["model3d"]["name"].endswith(".step")
    assert assets["model3d"]["origin"] == "SnapMagic zip"
    assert assets["model3d"]["source"] == "snapeda"
    assert assets["footprint"]["origin"] is None


async def test_spice_origin_falls_back_to_the_ng010_header(authed_client, full_token):
    async with mcp_session(full_token) as s:
        part_id = create_part(authed_client, "TLV431", mpn="SM010-TLV431")
        await call(
            s, "upload_eda_asset", kind="spice", filename="TLV431.lib",
            content_base64=_b64((SPICE / "tlv431_ng010.lib").read_bytes()),
            part_id=part_id,
        )
        eda = await call(s, "get_part_eda", part_id=part_id)

    spice = eda["assets"]["spice"]
    assert spice["origin_from"] == "spice_header"
    assert spice["origin"] == eda["spice_binding"]["model_source"]


# ---------------------------------------------------------------------
# Review: owner/admin session only
# ---------------------------------------------------------------------


async def test_owner_session_records_a_review_visible_over_mcp(
    authed_client, full_token, db
):
    async with mcp_session(full_token) as s:
        part_id = await _part_with_symbol_and_model(s, authed_client)
        sha = (await call(s, "get_part_eda", part_id=part_id))["assets"]["symbol"]["sha256"]

        listing = authed_client.get(f"/api/parts/{part_id}/eda/assets")
        r = _review(
            authed_client, part_id, "symbol", state="reviewed",
            against="datasheet rev C fig 12", sha256=sha,
        )
        eda = await call(s, "get_part_eda", part_id=part_id)

    assert listing.status_code == 200, listing.text
    assert listing.json()["data"]["can_review"] is True
    assert r.status_code == 200, r.text
    review = eda["assets"]["symbol"]["review"]
    assert review["state"] == "reviewed"
    assert review["against"] == "datasheet rev C fig 12"
    assert review["by_name"] == "Tester"
    assert review["by"] and review["at"]
    assert r.json()["data"]["asset"]["review"] == review

    rows = db.query(AuditLog).filter(AuditLog.action == "eda_asset_review.set").all()
    assert len(rows) == 1
    assert rows[0].comment == f"kind=symbol,sha256={sha},state=reviewed"

    # Back to unreviewed clears reviewer, time and `against`.
    reset = _review(authed_client, part_id, "symbol", state="unreviewed")
    assert reset.json()["data"]["asset"]["review"] == {
        "state": "unreviewed", "by": None, "by_name": None, "at": None, "against": None
    }


async def test_a_new_file_version_starts_unreviewed(authed_client, full_token, db):
    async with mcp_session(full_token) as s:
        part_id = await _part_with_symbol_and_model(s, authed_client)
        first = (await call(s, "get_part_eda", part_id=part_id))["assets"]["symbol"]
        await call(
            s, "set_asset_metadata", part_id_or_mpn=part_id, kind="symbol", origin="v1"
        )
        assert _review(authed_client, part_id, "symbol", state="rejected").status_code == 200

        # Renaming re-emits the stored file: same row, new bytes, new sha256.
        renamed = authed_client.patch(
            f"/api/eda/symbols/{first['id']}", json={"name": "OPA192_v2"}
        )
        assert renamed.status_code == 200, renamed.text
        second = (await call(s, "get_part_eda", part_id=part_id))["assets"]["symbol"]

    assert second["id"] == first["id"]
    assert second["sha256"] != first["sha256"]
    assert second["review"]["state"] == "unreviewed"
    assert second["origin"] is None
    # The old version's row is kept as history.
    old = db.query(EdaAssetMetadata).filter_by(sha256=first["sha256"]).one()
    assert old.review_state == "rejected"
    assert old.origin == "v1"


async def test_review_of_a_replaced_version_is_refused(authed_client, full_token):
    async with mcp_session(full_token) as s:
        part_id = await _part_with_symbol_and_model(s, authed_client)
    r = _review(authed_client, part_id, "symbol", state="reviewed", sha256="0" * 64)
    assert r.status_code == 409
    assert r.json()["code"] == "eda.asset_changed"


async def test_review_arguments_over_mcp_are_refused_and_write_nothing(
    authed_client, full_token, db
):
    async with mcp_session(full_token) as s:
        part_id = await _part_with_symbol_and_model(s, authed_client)
        state = await call_error(
            s, "set_asset_metadata", part_id_or_mpn=part_id, kind="symbol",
            origin="x", review_state="reviewed",
        )
        against = await call_error(
            s, "set_asset_metadata", part_id_or_mpn=part_id, kind="symbol",
            review_against="datasheet",
        )
    assert "eda.review_requires_owner" in state, state
    assert "eda.review_requires_owner" in against, against
    assert db.query(EdaAssetMetadata).count() == 0


async def test_rest_review_refuses_every_api_token(authed_client, full_token):
    """Even the OWNER's own full-access token cannot review."""
    async with mcp_session(full_token) as s:
        part_id = await _part_with_symbol_and_model(s, authed_client)
    token_client = _token_client(full_token)

    r = _review(token_client, part_id, "symbol", state="reviewed")
    listing = token_client.get(f"/api/parts/{part_id}/eda/assets")

    assert r.status_code == 403
    assert r.json()["code"] == "eda.review_requires_owner"
    assert listing.status_code == 200
    assert listing.json()["data"]["can_review"] is False


@pytest.mark.parametrize("role", ["member", "viewer"])
async def test_member_session_cannot_review(authed_client, full_token, role):
    async with mcp_session(full_token) as s:
        part_id = await _part_with_symbol_and_model(s, authed_client)
    joiner = _join_workspace(authed_client, role)

    r = _review(joiner, part_id, "symbol", state="reviewed")
    listing = joiner.get(f"/api/parts/{part_id}/eda/assets")

    assert r.status_code == 403
    assert r.json()["code"] in {
        "eda.review_requires_owner",
        "resource.insufficient_role",  # viewer: the router's member gate fires first
    }
    assert listing.json()["data"]["can_review"] is False


async def test_admin_session_can_review(authed_client, full_token):
    async with mcp_session(full_token) as s:
        part_id = await _part_with_symbol_and_model(s, authed_client)
    admin = _join_workspace(authed_client, "admin")
    r = _review(admin, part_id, "spice", state="reviewed", against="TI app note")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["asset"]["review"]["state"] == "reviewed"


async def test_member_token_may_write_metadata(authed_client):
    member = _join_workspace(authed_client, "member")
    token = _mint(member)["token"]
    async with mcp_session(_mint(authed_client)["token"]) as s:
        part_id = await _part_with_symbol_and_model(s, authed_client)
    async with mcp_session(token) as s:
        result = await call(
            s, "set_asset_metadata", part_id_or_mpn=part_id, kind="spice",
            model_kind="fitted",
        )
    assert result["asset"]["model_kind"] == "fitted"


async def test_read_only_token_is_refused_for_set_asset_metadata(authed_client, full_token, db):
    async with mcp_session(full_token) as s:
        part_id = await _part_with_symbol_and_model(s, authed_client)
    readonly = _mint(authed_client, read_only=True)["token"]
    async with mcp_session(readonly) as s:
        message = await call_error(
            s, "set_asset_metadata", part_id_or_mpn=part_id, kind="symbol", origin="x"
        )
        eda = await call(s, "get_part_eda", part_id=part_id)
    assert "auth.token_read_only" in message, message
    assert eda["assets"]["symbol"]["origin"] is None
    assert db.query(EdaAssetMetadata).count() == 0


# ---------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------


async def test_other_workspace_cannot_read_or_write(authed_client, full_token, db):
    async with mcp_session(full_token) as s:
        part_id = await _part_with_symbol_and_model(s, authed_client)
    other = TestClient(app)
    signup_user(other)
    other_token = _mint(other)["token"]

    async with mcp_session(other_token) as s:
        write = await call_error(
            s, "set_asset_metadata", part_id_or_mpn=part_id, kind="symbol", origin="x"
        )
        by_mpn = await call_error(
            s, "set_asset_metadata", part_id_or_mpn="SM010-OPA", kind="symbol", origin="x"
        )
    read = other.get(f"/api/parts/{part_id}/eda/assets")
    review = _review(other, part_id, "symbol", state="reviewed")

    assert "part.not_found" in write, write
    assert "part.not_found" in by_mpn, by_mpn
    assert read.status_code == 404
    assert review.status_code == 404
    assert db.query(EdaAssetMetadata).count() == 0
