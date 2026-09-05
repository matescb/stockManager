"""Migration 0074 is a widening, not a behaviour change.

The quantity columns are `Numeric(18,6)` from 0074 onward, but nothing
above the database has moved: the API still validates integers in and
still emits integers out, `current_quantity` still returns the same
ledger sum, and the 0013 non-negative trigger still stops an
over-removal. This file is the net under that claim — if a later step of
the units-of-measure track starts changing behaviour, it should have to
edit these assertions deliberately rather than drift past them.

The interesting failure mode a wider column introduces is *silent*: an
untyped route serialiser that used to hand FastAPI an `int` now hands it
a scaled `Decimal`, which `jsonable_encoder` renders as `5.0` rather
than `5`. `app/domain/_quantity.py::quantity_out` pins that back, and
several tests here assert on the concrete JSON type rather than on
equality (`5 == 5.0` in Python, so equality alone would not notice).
"""
from __future__ import annotations

import pytest

from tests._factories import add_stock, create_part, create_storage, signup_user


def _on_hand(client, part_id: str) -> int:
    r = client.get(f"/api/parts/{part_id}/stock")
    assert r.status_code == 200, r.text
    return r.json()["data"]["total_on_hand"]


def test_ledger_sum_and_current_quantity_are_unchanged(authed_client):
    part_id = create_part(authed_client, "Resistor")
    add_stock(authed_client, part_id, 10)

    r = authed_client.post("/api/stock/remove", json={"part_id": part_id, "quantity": 4})
    assert r.status_code == 200, r.text

    assert _on_hand(authed_client, part_id) == 6
    assert authed_client.get(f"/api/parts/{part_id}").json()["data"]["on_hand"] == 6


def test_ledger_sum_stays_exact_across_many_entries(authed_client):
    """SUM() over NUMERIC is exact and order-independent — the property
    the whole option-(a) schema choice rests on. A hundred adds and
    ninety-nine removes must land on exactly 1, not 0.9999999."""
    part_id = create_part(authed_client, "Capacitor")
    for _ in range(100):
        add_stock(authed_client, part_id, 3)
    for _ in range(99):
        r = authed_client.post(
            "/api/stock/remove", json={"part_id": part_id, "quantity": 3}
        )
        assert r.status_code == 200, r.text

    on_hand = _on_hand(authed_client, part_id)
    assert on_hand == 3
    assert isinstance(on_hand, int)


def test_negative_balance_protection_still_rejects_over_removal(authed_client):
    part_id = create_part(authed_client, "Diode")
    add_stock(authed_client, part_id, 5)

    r = authed_client.post("/api/stock/remove", json={"part_id": part_id, "quantity": 6})
    assert r.status_code in (400, 409), r.text
    # The refusal left the ledger alone.
    assert _on_hand(authed_client, part_id) == 5


@pytest.mark.parametrize("quantity", [2.5, "2.5", 0.1])
def test_api_still_refuses_fractional_input(authed_client, quantity):
    """The point of shipping the widening on its own: the column can hold
    2.5 but no request can put it there yet, so 0074 stays reversible."""
    part_id = create_part(authed_client, "Wire")
    r = authed_client.post(
        "/api/stock/add", json={"part_id": part_id, "quantity": quantity}
    )
    assert r.status_code == 422, r.text


def test_quantities_serialise_as_json_integers_not_floats(authed_client):
    """Every quantity that reaches the wire through an untyped serialiser
    must still be a JSON integer. `5 == 5.0` in Python, so these assert
    on the type, not the value."""
    storage_id = create_storage(authed_client, "Bin A")
    part_id = create_part(
        authed_client, "Inductor", low_stock_report_quantity=7, attrition_min_quantity=2
    )
    entry = add_stock(authed_client, part_id, 12, storage_id=storage_id, lot_name="L1")
    entry_data = entry.json()["data"]
    assert isinstance(entry_data["quantity_delta"], int)

    part = authed_client.get(f"/api/parts/{part_id}").json()["data"]
    assert isinstance(part["low_stock_report_quantity"], int)
    assert isinstance(part["attrition_min_quantity"], int)
    assert isinstance(part["on_hand"], int)

    stock = authed_client.get(f"/api/parts/{part_id}/stock").json()["data"]
    assert isinstance(stock["total_on_hand"], int)
    assert all(isinstance(row["quantity"], int) for row in stock["rows"])

    lots = authed_client.get(f"/api/parts/{part_id}/lots").json()["data"]
    assert lots and all(isinstance(lot["purchase_quantity"], int) for lot in lots)

    history = authed_client.get(
        "/api/stock/history", params={"part_id": part_id}
    ).json()["data"]
    assert history and all(isinstance(e["quantity_delta"], int) for e in history)

    activity = authed_client.get(f"/api/parts/{part_id}/activity").json()["data"]
    stock_events = [e for e in activity["events"] if e["kind"] == "stock"]
    assert stock_events
    assert all(isinstance(e["quantity_delta"], int) for e in stock_events)

    storage_parts = authed_client.get(f"/api/storage/{storage_id}/parts").json()["data"]
    assert storage_parts
    assert all(isinstance(row["quantity"], int) for row in storage_parts)

    storage_history = authed_client.get(
        f"/api/storage/{storage_id}/history"
    ).json()["data"]
    assert storage_history
    assert all(isinstance(e["quantity_delta"], int) for e in storage_history)


def test_order_quantities_serialise_as_json_integers(authed_client):
    part_id = create_part(authed_client, "Regulator")
    order_id = authed_client.post("/api/orders", json={"name": "PO-1"}).json()["data"]["id"]
    r = authed_client.post(
        f"/api/orders/{order_id}/entries",
        json={"part_id": part_id, "quantity_ordered": 25},
    )
    assert r.status_code in (200, 201), r.text

    order = authed_client.get(f"/api/orders/{order_id}").json()["data"]
    assert isinstance(order["order"]["totals"]["ordered"], int)
    assert isinstance(order["order"]["totals"]["received"], int)
    assert all(isinstance(e["quantity_ordered"], int) for e in order["entries"])
    assert all(isinstance(e["quantity_received"], int) for e in order["entries"])


def test_over_receive_error_message_still_reads_as_a_whole_number(authed_client):
    """`outstanding` is now `Decimal - Decimal`, and `orders.py` re-raises
    `OrderError` verbatim as the public 400 body — so an unguarded value
    would turn "outstanding 6" into "outstanding 6.000000" on the wire."""
    part_id = create_part(authed_client, "Connector")
    order_id = authed_client.post("/api/orders", json={"name": "PO-2"}).json()["data"]["id"]
    entry_id = authed_client.post(
        f"/api/orders/{order_id}/entries",
        json={"part_id": part_id, "quantity_ordered": 6},
    ).json()["data"]["id"]

    r = authed_client.post(
        f"/api/orders/{order_id}/receive",
        json={"lines": [{"order_entry_id": entry_id, "quantity": 10}]},
    )
    assert r.status_code == 400, r.text
    message = r.json()["status"]["message"]
    assert "outstanding 6," in message, message


def test_shortage_rows_serialise_as_json_integers(authed_client):
    """Whole-build and per-stage shortage rows both carry ledger sums, and
    both go on the wire through an untyped dict. `5 == 5.0` in Python, so
    these assert on the type."""
    c = authed_client
    part_id = create_part(c, "Transistor")
    add_stock(c, part_id, 4)
    project_id = c.post("/api/projects", json={"name": "P"}).json()["data"]["id"]
    entry_id = c.post(
        f"/api/projects/{project_id}/entries", json={"part_id": part_id, "quantity": 10}
    ).json()["data"]["id"]
    build_id = c.post(
        "/api/builds", json={"name": "B", "project_id": project_id, "quantity": 1}
    ).json()["data"]["id"]

    def _assert_int_row(row):
        for key in ("required", "available", "substitute_available", "short_by"):
            assert isinstance(row[key], int), (key, row[key])

    detail = c.get(f"/api/builds/{build_id}").json()["data"]
    assert detail["shortage"]
    for row in detail["shortage"]:
        _assert_int_row(row)

    report = c.get(
        "/api/reports/bom-shortage", params={"project_id": project_id, "quantity": 1}
    ).json()["data"]
    assert isinstance(report["total_short"], int)
    for row in report["rows"]:
        _assert_int_row(row)

    r = c.post(
        f"/api/builds/{build_id}/stages",
        json={"name": "SMT", "lines": [{"project_entry_id": entry_id, "portion_pct": 100}]},
    )
    assert r.status_code == 201, r.text
    stages = c.get(f"/api/builds/{build_id}/stages").json()["data"]
    assert stages and stages[0]["shortage"]
    for row in stages[0]["shortage"]:
        _assert_int_row(row)


def test_stock_reads_stay_workspace_isolated(authed_client, client):
    """The widened columns and the new `unit` stamp change no query path,
    but the ledger reads they feed are the ones workspace isolation is
    load-bearing for — so re-pin it here rather than assume."""
    part_a = create_part(authed_client, "Owned by A")
    add_stock(authed_client, part_a, 42)

    signup_user(client)  # a second user => a second workspace

    assert client.get(f"/api/parts/{part_a}").status_code == 404
    assert client.get(f"/api/parts/{part_a}/stock").status_code == 404
    assert client.get(f"/api/parts/{part_a}/lots").status_code == 404

    # B's own ledger is empty — A's 42 must not leak into any roll-up.
    assert client.get("/api/parts").json()["data"] == []
    assert client.get("/api/stock/history").json()["data"] == []
    assert client.get("/api/lots").json()["data"] == []

    # ...and A still sees its own.
    assert _on_hand(authed_client, part_a) == 42
