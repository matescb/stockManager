"""Shared helpers for the box intake / audit tests (phase 15)."""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.domain.parts.services.bag_signature import compute_bag_signature
from app.main import app


def signup_client(provider: bool = True) -> TestClient:
    c = TestClient(app)
    r = c.post(
        "/api/auth/signup",
        json={
            "email": f"u-{uuid.uuid4().hex[:8]}@x.com",
            "name": "u",
            "password": "TestPass-2026-Stronk",
        },
    )
    assert r.status_code == 200, r.text
    if provider:
        c.patch(
            "/api/workspaces/current",
            json={"parts_provider": "mouser", "parts_provider_api_key": "fake-key"},
        )
    return c


def mouser_stub(mpn: str) -> dict:
    return {
        "Errors": [],
        "SearchResults": {
            "NumberOfResult": 1,
            "Parts": [{
                "Manufacturer": "Yageo",
                "ManufacturerPartNumber": mpn,
                "Description": "Thick Film Resistors - SMD 0402",
                "Category": "Resistors",
                "ProductAttributes": [],
            }],
        },
    }


def no_match(url, payload):
    return {"Errors": [], "SearchResults": {"NumberOfResult": 0, "Parts": []}}


def box(c: TestClient, name: str) -> str:
    r = c.post("/api/storage", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def part(c: TestClient, mpn: str) -> str:
    r = c.post("/api/parts", json={"name": mpn, "mpn": mpn})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def raw_bag(mpn: str, qty: int, serial: str = "") -> str:
    return f"[)>\x1e06\x1d1P{mpn}\x1dQ{qty}\x1d1T{serial or uuid.uuid4().hex[:6]}\x1e\x04"


def bag_fields(raw: str) -> dict:
    return {"raw_bag_code": raw, "bag_signature": compute_bag_signature(raw)}


def intake(c: TestClient, box_id: str, **body):
    return c.post(f"/api/storage/{box_id}/intake", json=body)


def bags(c: TestClient, box_id: str) -> list[dict]:
    r = c.get(f"/api/storage/{box_id}/bags")
    assert r.status_code == 200, r.text
    return r.json()["data"]
