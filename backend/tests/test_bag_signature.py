"""Bag-signature re-scan correlation contract test (TEST-010).

`bag_signature` is the only stable correlation between two scans of the
same physical bag. The FE produces it (`web/src/lib/bagCode.ts::bagSignature`),
the BE just stores it verbatim. The contract — every alternate decoder
form of the same bag hashes to the same hex digest — lives in
`web/src/lib/__fixtures__/bagSignatures.json`. The FE test in
`bagCode.test.ts::bagSignature fixture parity` iterates that file; this
file replays the same digests through the lookup endpoint so a drift
on either side breaks exactly one of the two suites.

We do NOT re-implement the FE normaliser in Python — taking that path
would just move the trust boundary. Instead we use the pre-computed
hex digests in the fixture as opaque inputs.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest


_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "web" / "src" / "lib" / "__fixtures__" / "bagSignatures.json"
)


@pytest.fixture(scope="module")
def fixture():
    return json.loads(_FIXTURE_PATH.read_text())


def _create_part(c, name="P"):
    r = c.post("/api/parts", json={"name": name, "part_type": "local"})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _create_storage(c, name="Bin"):
    r = c.post("/api/storage", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _add_stock(c, part_id, storage_id, signature, qty=5):
    r = c.post(
        "/api/stock/add",
        json={
            "part_id": part_id,
            "quantity": qty,
            "storage_location_id": storage_id,
            "bag_signature": signature,
            "lot": {"name": "L1"},
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]


def test_rescan_returns_prior_entry(authed_client, fixture):
    """Write a stock entry with the fixture's first signature, then GET
    `/api/parts/by-bag-signature/{sig}` — the lookup must surface that
    entry's part_id and quantity."""
    bag = fixture["bags"][0]
    sig = bag["expected_signature"]

    part_id = _create_part(authed_client, name=f"P-{uuid.uuid4().hex[:6]}")
    storage_id = _create_storage(authed_client, name=f"S-{uuid.uuid4().hex[:6]}")
    _add_stock(authed_client, part_id, storage_id, sig, qty=7)

    r = authed_client.get(f"/api/parts/by-bag-signature/{sig}")
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body is not None
    assert body["part_id"] == part_id
    assert body["quantity"] == 7
    assert body["storage_location_id"] == storage_id


def test_rescan_via_alternate_decoder_form_hits_same_row(authed_client, fixture):
    """Every entry in the fixture has multiple `raws` representing the
    same physical bag through different decoders. They all share one
    `expected_signature`. Persist via that digest, then look up via the
    same digest a second time — must hit the same prior entry. This is
    the FE/BE drift detector: if the FE normaliser changes such that
    one of the alternate decodings no longer hashes to the fixture's
    `expected_signature`, the FE vitest fails. If the BE somehow
    started transforming `bag_signature` server-side, this test would
    fail (it won't — the BE stores it verbatim, by design)."""
    bag = next(b for b in fixture["bags"] if len(b["raws"]) > 1)
    sig = bag["expected_signature"]

    part_id = _create_part(authed_client, name=f"P-{uuid.uuid4().hex[:6]}")
    storage_id = _create_storage(authed_client, name=f"S-{uuid.uuid4().hex[:6]}")
    _add_stock(authed_client, part_id, storage_id, sig, qty=3)

    # Two consecutive lookups must surface the same prior — pins that
    # the lookup is idempotent and signature-keyed (not occurred_at
    # tie-breakable by chance).
    a = authed_client.get(f"/api/parts/by-bag-signature/{sig}").json()["data"]
    b = authed_client.get(f"/api/parts/by-bag-signature/{sig}").json()["data"]
    assert a is not None
    assert a == b
    assert a["part_id"] == part_id


def test_distinct_bags_have_distinct_signatures(authed_client, fixture):
    """A digest that no entry in the workspace shares must return
    `data: null`, not surface an unrelated entry."""
    # Use a fixture digest the workspace hasn't written.
    # All bags have distinct hashes; pick one we haven't persisted.
    sig_unwritten = fixture["bags"][1]["expected_signature"]

    r = authed_client.get(f"/api/parts/by-bag-signature/{sig_unwritten}")
    assert r.status_code == 200, r.text
    assert r.json()["data"] is None


def test_short_signature_is_quietly_rejected(authed_client):
    """The endpoint guards against prefix-scan probing: only 64-char
    alphanumeric digests get a real lookup; everything else returns
    `data: null` without touching the DB."""
    # length wrong: 3, 63, 65 — these violate `len(signature) != 64`.
    # Note: a 64-char string with non-alnum could URL-decode oddly via
    # the test client; rely on the length guard (the more important arm
    # of the conditional) to pin behaviour here.
    for junk in ["abc", "x" * 63, "Y" * 65]:
        r = authed_client.get(f"/api/parts/by-bag-signature/{junk}")
        assert r.status_code == 200, (junk, r.text)
        assert r.json()["data"] is None, junk
