from __future__ import annotations

from tests._factories import create_part


def _priced_payload(part_id: str, currency: str) -> dict:
    return {
        "part_id": part_id,
        "quantity": 1,
        "price": {
            "mode": "per_component",
            "unit_price": "1.00",
            "currency": currency,
        },
    }


def test_add_stock_rejects_currency_outside_workspace_active_list(authed_client):
    part_id = create_part(authed_client, name="Currency policy part")
    patch = authed_client.patch("/api/workspaces/current", json={"active_currencies": ["EUR"]})
    assert patch.status_code == 200, patch.text

    response = authed_client.post("/api/stock/add", json=_priced_payload(part_id, "USD"))

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "stock.invalid_currency"
    assert body["field"] == "price.currency"
    assert body["value"] == "USD"
    assert body["active_currencies"] == ["EUR"]

    stock = authed_client.get(f"/api/parts/{part_id}/stock")
    assert stock.status_code == 200, stock.text
    assert stock.json()["data"]["total_on_hand"] == 0


def test_add_stock_rejects_unknown_three_letter_currency(authed_client):
    part_id = create_part(authed_client, name="Unknown currency part")

    response = authed_client.post("/api/stock/add", json=_priced_payload(part_id, "XYZ"))

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "stock.invalid_currency"
    assert body["value"] == "XYZ"


def test_add_stock_accepts_active_currency(authed_client):
    part_id = create_part(authed_client, name="Active currency part")
    patch = authed_client.patch("/api/workspaces/current", json={"active_currencies": ["EUR"]})
    assert patch.status_code == 200, patch.text

    response = authed_client.post("/api/stock/add", json=_priced_payload(part_id, "eur"))

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["currency"] == "EUR"
    assert body["unit_price"] == 1.0
