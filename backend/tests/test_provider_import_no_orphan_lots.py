from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.domain.lots.models import Lot
from app.domain.stock.models import StockEntry
from app.domain.stock.service import StockError
from app.main import app


def _signup(c: TestClient) -> None:
    r = c.post(
        "/api/auth/signup",
        json={
            "email": f"u-{uuid.uuid4().hex[:8]}@x.com",
            "name": "u",
            "password": "TestPass-2026-Stronk",
        },
    )
    assert r.status_code == 200, r.text


def _stub_mouser_response(mpn: str = "RC0402JR-070R") -> dict:
    return {
        "Errors": [],
        "SearchResults": {
            "NumberOfResult": 1,
            "Parts": [
                {
                    "Manufacturer": "Yageo",
                    "ManufacturerPartNumber": mpn,
                    "Description": "Thick Film Resistors - SMD 0R 1/16W 5% 0402",
                    "Category": "Resistors",
                    "DataSheetUrl": "https://example.com/ds.pdf",
                    "ImagePath": "https://example.com/img.jpg",
                    "ProductDetailUrl": f"https://www.mouser.com/{mpn}",
                    "ProductAttributes": [],
                }
            ],
        },
    }


def test_savepoint_rollback_on_stock_error(db, monkeypatch):
    c = TestClient(app)
    _signup(c)
    r = c.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": "fake-key"},
    )
    assert r.status_code == 200, r.text

    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_mouser_response(
            mpn=payload["SearchByPartRequest"]["mouserPartNumber"]
        ),
    )

    def stock_entry_failure(*args, **kwargs):
        raise StockError("simulated stock entry failure")

    monkeypatch.setattr("app.domain.stock.service.StockEntry", stock_entry_failure)

    r = c.post(
        "/api/parts/bulk-import-from-scan",
        json={
            "rows": [
                {
                    "mpn": "RC0402JR-070R",
                    "quantity": 1,
                    "lot_name": "lot-created-before-stock-entry",
                }
            ]
        },
    )

    assert r.status_code == 200, r.text
    row = r.json()["data"]["rows"][0]
    assert row["status"] == "created"
    assert row["quantity_added"] == 0
    assert row["stock_error"] == "simulated stock entry failure"

    part_id = uuid.UUID(row["part_id"])
    lot_count = db.execute(
        select(func.count()).select_from(Lot).where(Lot.part_id == part_id)
    ).scalar_one()
    stock_count = db.execute(
        select(func.count()).select_from(StockEntry).where(StockEntry.part_id == part_id)
    ).scalar_one()

    assert lot_count == 0
    assert stock_count == 0
