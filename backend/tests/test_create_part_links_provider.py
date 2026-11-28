"""A part created with an MPN is linked to the distributors, or refused.

Owner's rule (2026-11-28): a part he cannot buy has no place in the library.
Both create doors share `create_part.py`, so both are pinned here.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.domain.parts.models import Part
from tests.test_mcp import call, call_error, mcp_session
from tests.test_mcp_catalog import (  # noqa: F401 - fixtures
    MPN,
    _digikey_primary,
    _stub_digikey,
    clock,
    token,
)

pytestmark = pytest.mark.link_on_create


def _parts(db, mpn: str) -> int:
    return db.execute(select(func.count()).select_from(Part).where(Part.mpn == mpn)).scalar()


def test_rest_create_links_the_part_and_stores_its_prices(
    authed_client, monkeypatch, clock  # noqa: F811
):
    _digikey_primary(authed_client)
    calls = _stub_digikey(monkeypatch)
    r = authed_client.post("/api/parts", json={"mpn": MPN, "description": "my words"})
    assert r.status_code == 201, r.text
    assert calls == [MPN]
    part = authed_client.get(f"/api/parts/{r.json()['data']['id']}").json()["data"]
    assert part["linked_provider"] == "digikey"
    assert part["part_type"] == "linked"
    assert part["description"] == "my words", "the claim fills only what was left empty"
    assert part["manufacturer"] == "Texas Instruments"
    assert [b["min_qty"] for b in part["prices"][0]["breaks"]] == [1, 10]


async def test_mcp_create_after_catalog_lookup_spends_no_second_call(
    authed_client, token, monkeypatch, clock  # noqa: F811
):
    _digikey_primary(authed_client)
    calls = _stub_digikey(monkeypatch)
    async with mcp_session(token) as s:
        await call(s, "catalog_lookup", mpn=MPN)
        out = await call(s, "create_part", mpn=MPN)
    assert out["created"] is True
    assert calls == [MPN], "the create reused the lookup's answer"
    part = authed_client.get(f"/api/parts/{out['part']['id']}").json()["data"]
    assert part["linked_provider"] == "digikey"


async def test_an_mpn_no_distributor_sells_is_refused_on_both_doors(
    authed_client, token, db, monkeypatch, clock  # noqa: F811
):
    _digikey_primary(authed_client)
    _stub_digikey(monkeypatch, status=404)
    r = authed_client.post("/api/parts", json={"mpn": "NOBODY-SELLS-1"})
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "part.not_available_from_providers"
    assert r.json()["providers"][0]["provider"] == "digikey"
    assert _parts(db, "NOBODY-SELLS-1") == 0

    clock.now += 5  # past the agent pacing spacing
    async with mcp_session(token) as s:
        err = await call_error(s, "create_part", mpn="NOBODY-SELLS-2")
    assert "part.not_available_from_providers" in err
    assert _parts(db, "NOBODY-SELLS-2") == 0


def test_a_distributor_that_cannot_answer_is_not_read_as_does_not_exist(
    authed_client, db, monkeypatch, clock  # noqa: F811
):
    _digikey_primary(authed_client)
    _stub_digikey(monkeypatch, status=500)
    r = authed_client.post("/api/parts", json={"mpn": MPN})
    assert r.status_code == 503, r.text
    assert _parts(db, MPN) == 0


def test_a_part_without_mpn_stays_local(authed_client, monkeypatch, clock):  # noqa: F811
    _digikey_primary(authed_client)
    calls = _stub_digikey(monkeypatch)
    r = authed_client.post("/api/parts", json={"name": "Test pad, round 1 mm"})
    assert r.status_code == 201, r.text
    assert r.json()["data"]["part_type"] == "local"
    assert calls == []


def test_without_any_provider_configured_create_is_unchanged(authed_client, monkeypatch):
    calls = _stub_digikey(monkeypatch)
    r = authed_client.post("/api/parts", json={"mpn": "ANY-MPN-1"})
    assert r.status_code == 201, r.text
    assert r.json()["data"]["linked_provider"] is None
    assert calls == []
