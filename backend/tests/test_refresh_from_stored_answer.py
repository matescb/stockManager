"""edaflow SM-017 follow-up — link a part from the lookup answer already held.

netsch looks a part up with MCP `catalog_lookup`, then creates it with a
plain `POST /api/parts`, so the answer's price breaks were never stored.
`POST /api/parts/{id}/refresh-from-provider?stored_answer=true` applies the
answer this workspace already cached: no provider call, no asset download,
exact MPN only, and `last_refresh_at` is when the answer was fetched.
"""
from __future__ import annotations

from datetime import datetime

import app.domain.parts.providers.digikey as dk
from tests.test_mcp import call, mcp_session
from tests.test_mcp_catalog import (  # noqa: F401 - fixtures
    MPN,
    _digikey_primary,
    _stub_digikey,
    clock,
    token,
)


def _refuse_digikey(monkeypatch) -> None:
    def refuse(*a, **k):
        raise AssertionError("stored_answer must not call DigiKey")
    monkeypatch.setattr(dk, "_get_product_details", refuse)
    monkeypatch.setattr(dk, "_post_token", refuse)
    monkeypatch.setattr(
        "app.domain.parts.services.assets.fetch_provider_asset", refuse
    )


def _create(c, mpn: str) -> str:
    r = c.post("/api/parts", json={"name": mpn, "mpn": mpn, "part_type": "local"})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


async def test_stored_answer_links_the_part_and_stores_its_price_breaks(
    authed_client, token, monkeypatch, clock  # noqa: F811
):
    _digikey_primary(authed_client)
    calls = _stub_digikey(monkeypatch)
    async with mcp_session(token) as s:
        looked = await call(s, "catalog_lookup", mpn=MPN)
    assert calls == [MPN]
    retrieved_at = looked["records"][0]["retrieved_at"]

    pid = _create(authed_client, MPN)
    _refuse_digikey(monkeypatch)
    r = authed_client.post(f"/api/parts/{pid}/refresh-from-provider?stored_answer=true")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["found"] is True

    part = authed_client.get(f"/api/parts/{pid}").json()["data"]
    assert part["linked_provider"] == "digikey"
    [ladder] = part["prices"]
    assert ladder["provider"] == "digikey" and ladder["currency"] == "CZK"
    assert ladder["breaks"] == [
        {"min_qty": 1, "unit_price": 52.1},
        {"min_qty": 10, "unit_price": 41.5},
    ]
    # Stamped with the answer's own fetch time, not the time of the write.
    assert datetime.fromisoformat(ladder["refreshed_at"]) == datetime.fromisoformat(
        retrieved_at
    )


def test_without_a_stored_answer_it_refuses_and_calls_nothing(
    authed_client, monkeypatch
):
    _digikey_primary(authed_client)
    _refuse_digikey(monkeypatch)
    pid = _create(authed_client, "NEVER-LOOKED-UP-1")
    r = authed_client.post(f"/api/parts/{pid}/refresh-from-provider?stored_answer=true")
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "part.provider_no_stored_answer"
    assert authed_client.get(f"/api/parts/{pid}").json()["data"]["linked_provider"] is None
