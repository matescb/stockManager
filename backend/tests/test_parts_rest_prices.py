"""edaflow SM-017 / SM-018 — stored prices and curated specs on the parts REST record.

Providers store a price ladder as display strings, one row per tier
(DigiKey `"0.5040 CZK"`, Mouser `"0,017 €"`), and a secondary writes the same
key under its namespace. `prices` reads them back structured, from stored
rows only: the provider client is made to fail so a call would show.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from app.domain.custom_fields.models import CustomField
from app.domain.parts.models import Part, PartProviderLink
from app.domain.parts.services.stored_prices import parse_price
from app.infra.db import SessionLocal
from app.main import app
from tests._factories import signup_user

MAY = datetime(2026, 5, 10, 15, 47, tzinfo=UTC)
SEPT = datetime(2026, 9, 18, 0, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_provider_calls(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("prices must not call a provider")
    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)


def _authed() -> TestClient:
    c = TestClient(app)
    signup_user(c)
    return c


def _part(c: TestClient, mpn: str) -> str:
    r = c.post("/api/parts", json={"name": mpn, "part_type": "local", "mpn": mpn})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _seed(part_id: str, *, linked: str | None, refreshed=None, rows=(), links=()) -> None:
    """Write provider rows the way a refresh leaves them."""
    with SessionLocal() as db:
        part = db.get(Part, uuid.UUID(part_id))
        part.linked_provider = linked
        part.last_refresh_at = refreshed
        for key, value, stamp in rows:
            db.add(CustomField(
                workspace_id=part.workspace_id, object_type="part", object_id=part.id,
                key=key, value=value, source="provider", provider=stamp,
            ))
        for provider, at in links:
            db.add(PartProviderLink(
                workspace_id=part.workspace_id, part_id=part.id, provider=provider,
                last_refresh_at=at,
            ))
        db.commit()


def test_digikey_primary_and_secondary_ladders_both_come_back_newest_first():
    c = _authed()
    pid = _part(c, "GRM155R71H104KE14D")
    _seed(
        pid, linked="digikey", refreshed=MAY,
        rows=[
            # Un-namespaced, written before rows carried a provider stamp.
            ("Unit price (1+)", "2.1000 CZK", None),
            ("Unit price (10+)", "0.5040 CZK", None),
            ("digikey:Unit price (1+)", "2.0700 CZK", "digikey"),
            ("digikey:Unit price (10+)", "0.5370 CZK", "digikey"),
            ("digikey:Unit price (50+)", "0.3510 CZK", "digikey"),
            ("digikey:Unit price (100+)", "0.2953 CZK", "digikey"),
            ("digikey:Unit price (500+)", "0.2078 CZK", "digikey"),
            ("package", "0402 (1005 Metric)", "digikey"),
        ],
        links=[("digikey", SEPT)],
    )
    prices = c.get(f"/api/parts/{pid}").json()["data"]["prices"]
    assert prices == [
        {
            "provider": "digikey", "source": "secondary", "currency": "CZK",
            "breaks": [
                {"min_qty": 1, "unit_price": 2.07},
                {"min_qty": 10, "unit_price": 0.537},
                {"min_qty": 50, "unit_price": 0.351},
                {"min_qty": 100, "unit_price": 0.2953},
                {"min_qty": 500, "unit_price": 0.2078},
            ],
            "refreshed_at": SEPT.isoformat(),
            "unparsed": 0,
        },
        {
            "provider": "digikey", "source": "primary", "currency": "CZK",
            "breaks": [
                {"min_qty": 1, "unit_price": 2.1},
                {"min_qty": 10, "unit_price": 0.504},
            ],
            "refreshed_at": MAY.isoformat(),
            "unparsed": 0,
        },
    ]
    listed = {r["id"]: r for r in c.get("/api/parts").json()["data"]}
    assert listed[pid]["prices"] == prices


def test_mouser_decimal_comma_euro_and_an_unreadable_value():
    c = _authed()
    pid = _part(c, "CRCW0402100RFKED")
    _seed(
        pid, linked="mouser", refreshed=SEPT,
        rows=[
            ("Unit price (10+)", "0,017 €", "mouser"),
            ("Unit price (100+)", "0,008 €", "mouser"),
            ("Unit price (1000+)", "1.234,50 €", "mouser"),  # refused, not misread
        ],
    )
    [entry] = c.get(f"/api/parts/{pid}").json()["data"]["prices"]
    assert entry["provider"] == "mouser"
    assert entry["currency"] == "EUR"
    assert entry["breaks"] == [
        {"min_qty": 10, "unit_price": 0.017},
        {"min_qty": 100, "unit_price": 0.008},
    ]
    assert entry["unparsed"] == 1


def test_a_part_without_provider_data_has_an_empty_list():
    c = _authed()
    pid = _part(c, "NOPRICE-1")
    assert c.get(f"/api/parts/{pid}").json()["data"]["prices"] == []
    assert c.get("/api/parts").json()["data"][0]["prices"] == []


@pytest.mark.parametrize(("raw", "want"), [
    ("2.1000 CZK", (Decimal("2.1000"), "CZK")),
    ("0,017 €", (Decimal("0.017"), "EUR")),
    ("$0.25", (Decimal("0.25"), "USD")),
    ("12 Kč", (Decimal("12"), "CZK")),
    ("1.234,50 €", None),
    ("0.10", None),
    ("€0.10 EUR", None),
    ("0 CZK", None),
    ("call", None),
    (None, None),
])
def test_parse_price(raw, want):
    assert parse_price(raw) == want


def test_curated_specs_drop_namespaced_and_catalog_keys():
    c = _authed()
    pid = _part(c, "GRM155R71H104KE14D")
    _seed(pid, linked="digikey", refreshed=MAY, rows=[
        ("tolerance", "±10%", "digikey"),
        ("dielectric", "X7R", "digikey"),
        ("voltage_rating", "50 V", "digikey"),
        ("package", "0402 (1005 Metric)", "digikey"),
        ("Unit price (10+)", "0.5040 CZK", None),
        ("digikey:Unit price (10+)", "0.5370 CZK", "digikey"),
        ("digikey:Lifecycle", "Active", "digikey"),
        ("image_url", "https://example.invalid/a.jpg", "digikey"),
        ("RoHS", "Compliant", "digikey"),
    ])
    want = {
        "dielectric": "X7R", "package": "0402 (1005 Metric)",
        "tolerance": "±10%", "voltage_rating": "50 V",
    }
    assert c.get(f"/api/parts/{pid}").json()["data"]["curated_specs"] == want
    listed = {r["id"]: r for r in c.get("/api/parts").json()["data"]}
    assert listed[pid]["curated_specs"] == want
    bare = _part(c, "NOSPEC-1")
    assert c.get(f"/api/parts/{bare}").json()["data"]["curated_specs"] == {}
