"""A raw ECIA bag-label string is never sent to a parts provider as an MPN.

A Mouser label can carry no 1P (manufacturer P/N) at all; an older web
client then passed the whole `[)>06K…` string on as the MPN, which spent a
metered DigiKey / Mouser call on a guaranteed miss and told the operator
"not in the library". The provider-cache choke point and the box-scan
endpoints refuse such a string up front.
"""
from __future__ import annotations

from tests._box_scan_helpers import (
    bag_fields,
    box,
    intake,
    part,
    raw_bag,
    signup_client,
)

RAW_NO_1P = "[)>06K4048970914K027PC201,C202,C503Q10011K0925716424LJP1VMurata"


def _provider_must_not_be_called(monkeypatch) -> None:
    def boom(url, payload):  # pragma: no cover - reaching here is the failure
        raise AssertionError("provider called with a bag-label string")

    monkeypatch.setattr("app.domain.parts.providers.mouser._post_mouser", boom)


def test_resolve_with_bag_label_as_mpn_is_not_found_without_provider_call(monkeypatch):
    _provider_must_not_be_called(monkeypatch)
    c = signup_client()
    b = box(c, "Box A")

    r = c.post(f"/api/storage/{b}/scan/resolve", json={"mpn": RAW_NO_1P})

    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "not_found"


def test_intake_with_bag_label_as_mpn_is_422_without_provider_call(monkeypatch):
    _provider_must_not_be_called(monkeypatch)
    c = signup_client()
    b = box(c, "Box A")

    r = intake(c, b, mpn=RAW_NO_1P, quantity=100)

    assert r.status_code == 422, r.text
    assert "no 1P" in r.text


def test_lookup_mpn_refuses_bag_label_without_provider_call(monkeypatch):
    _provider_must_not_be_called(monkeypatch)
    c = signup_client()

    r = c.post("/api/parts/lookup-mpn", json={"mpn": RAW_NO_1P})

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["found"] is False
    assert "bag label" in data["message"]


def test_known_bag_resolves_by_signature_without_an_mpn():
    # A no-MPN label that is already recorded still resolves — the web
    # client sends an empty MPN plus the signature.
    c = signup_client(provider=False)
    b = box(c, "Box A")
    part(c, "LM358")
    raw = raw_bag("LM358", 10)
    intake(c, b, mpn="LM358", quantity=10, **bag_fields(raw))

    r = c.post(f"/api/storage/{b}/scan/resolve", json={"mpn": "", **bag_fields(raw)})

    assert r.json()["data"]["status"] == "bag_here"
