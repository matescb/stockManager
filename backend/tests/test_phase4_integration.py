from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import app.core.ratelimit as _ratelimit_mod
from app.core.time import utcnow
from app.domain.lots.models import Lot
from app.domain.orders.models import Order, OrderEntry
from app.domain.sourcing.budget import BUDGET
from app.domain.sourcing.models import PurchasePlan
from app.domain.stock.models import StockEntry
from app.main import app
from tests._factories import add_stock, create_part, create_project_with_bom, signup_user
from tests.test_purchase_plan_route import (
    _configure_sourcing,
    _FakeTrustedPartsClient,
    _offer,
    _post_plan,
)


@pytest.fixture(autouse=True)
def reset_sourcing_state(monkeypatch):
    original_limiter_enabled = _ratelimit_mod.limiter.enabled
    _ratelimit_mod.limiter.enabled = False
    _FakeTrustedPartsClient.calls = []
    _FakeTrustedPartsClient.offers_by_mpn = {}
    BUDGET._events.clear()
    try:
        _ratelimit_mod.limiter.reset()
    except Exception:
        pass
    monkeypatch.setattr(
        "app.domain.sourcing.service.make_sourcing_provider",
        lambda workspace: _FakeTrustedPartsClient(workspace.id),
    )
    yield
    _ratelimit_mod.limiter.enabled = original_limiter_enabled
    BUDGET._events.clear()
    try:
        _ratelimit_mod.limiter.reset()
    except Exception:
        pass


def _refresh(client: TestClient, plan_id: str):
    return client.post(f"/api/sourcing/purchase-plans/{plan_id}/refresh")


def _convert(client: TestClient, plan_id: str):
    return client.post(f"/api/sourcing/purchase-plans/{plan_id}/orders")


def _signup_client(email_prefix: str) -> TestClient:
    client = TestClient(app)
    signup_user(client, email=f"{email_prefix}-{uuid.uuid4().hex[:8]}@example.com")
    return client


def _workspace_id(client: TestClient) -> uuid.UUID:
    r = client.get("/api/workspaces/current")
    assert r.status_code == 200, r.text
    return uuid.UUID(r.json()["data"]["id"])


def _stock_snapshot(db, workspace_id: uuid.UUID) -> list[tuple[Any, ...]]:
    rows = db.execute(
        select(
            StockEntry.id,
            StockEntry.part_id,
            StockEntry.lot_id,
            StockEntry.quantity_delta,
            StockEntry.status,
            StockEntry.operation_type,
            StockEntry.order_id,
            StockEntry.order_entry_id,
        )
        .where(StockEntry.workspace_id == workspace_id)
        .order_by(StockEntry.id)
    ).all()
    return [tuple(row) for row in rows]


def _lot_snapshot(db, workspace_id: uuid.UUID) -> list[tuple[Any, ...]]:
    rows = db.execute(
        select(
            Lot.id,
            Lot.part_id,
            Lot.name,
            Lot.source_type,
            Lot.source_order_id,
            Lot.purchase_quantity,
            Lot.purchase_unit_cost,
            Lot.purchase_currency,
        )
        .where(Lot.workspace_id == workspace_id)
        .order_by(Lot.id)
    ).all()
    return [tuple(row) for row in rows]


def _single_line_plan(
    client: TestClient,
    *,
    mpn: str,
    quantity: int = 5,
    strategy: str = "preferred_first",
    distributor: str = "DigiKey",
    unit_price: float = 1.0,
):
    _configure_sourcing(client, preferred=["DigiKey"])
    part_id = create_part(client, name=mpn, mpn=mpn)
    project_id = create_project_with_bom(
        client,
        f"Phase 4 {mpn}",
        [{"part_id": part_id, "quantity": quantity}],
    )
    _FakeTrustedPartsClient.offers_by_mpn = {
        mpn: [_offer(mpn, distributor=distributor, stock=100, unit_price=unit_price)]
    }
    created = _post_plan(client, project_id, strategy=strategy)
    assert created.status_code == 200, created.text
    return created.json()["data"], part_id


def _single_line_refreshed_plan(
    client: TestClient,
    *,
    mpn: str,
    quantity: int = 5,
    strategy: str = "preferred_first",
    distributor: str = "DigiKey",
    unit_price: float = 1.0,
):
    created, part_id = _single_line_plan(
        client,
        mpn=mpn,
        quantity=quantity,
        strategy=strategy,
        distributor=distributor,
        unit_price=unit_price,
    )
    refreshed = _refresh(client, created["id"])
    assert refreshed.status_code == 200, refreshed.text
    return refreshed.json()["data"], part_id


def _two_distributor_refreshed_plan(client: TestClient, *, prefix: str = "PH4-TWO"):
    _configure_sourcing(client, preferred=["DigiKey"])
    part_a = create_part(client, name=f"{prefix} A", mpn=f"{prefix}-A")
    part_b = create_part(client, name=f"{prefix} B", mpn=f"{prefix}-B")
    project_id = create_project_with_bom(
        client,
        f"Phase 4 {prefix}",
        [{"part_id": part_a, "quantity": 5}, {"part_id": part_b, "quantity": 7}],
    )
    _FakeTrustedPartsClient.offers_by_mpn = {
        f"{prefix}-A": [_offer(f"{prefix}-A", distributor="DigiKey", stock=100, unit_price=1.0)],
        f"{prefix}-B": [_offer(f"{prefix}-B", distributor="Mouser", stock=100, unit_price=2.0)],
    }
    created = _post_plan(client, project_id)
    assert created.status_code == 200, created.text
    refreshed = _refresh(client, created.json()["data"]["id"])
    assert refreshed.status_code == 200, refreshed.text
    return refreshed.json()["data"]


def test_full_pipeline_preferred_first_strategy(
    authed_client,
    db,
):
    _configure_sourcing(authed_client, preferred=["DigiKey"])
    workspace_id = _workspace_id(authed_client)
    part_id = create_part(authed_client, name="Integration IC", mpn="PH4-IC")
    add_stock(authed_client, part_id, 2, lot_name="Existing PH4 lot")
    project_id = create_project_with_bom(
        authed_client,
        "Phase 4 integration BOM",
        [{"part_id": part_id, "quantity": 5}],
    )

    initial_url = "https://www.trustedparts.com/PH4-IC/DigiKey"
    refreshed_url = "https://www.trustedparts.com/PH4-IC/DigiKey"
    _FakeTrustedPartsClient.offers_by_mpn = {
        "PH4-IC": [_offer("PH4-IC", distributor="DigiKey", stock=50, unit_price=1.2345)]
    }
    created = _post_plan(authed_client, project_id, build_quantity=1)
    assert created.status_code == 200, created.text
    created_plan = created.json()["data"]
    assert created_plan["status"] == "draft"
    assert created_plan["lines"][0]["internal_available_qty"] == 2
    assert created_plan["lines"][0]["shortage_qty"] == 3
    assert created_plan["lines"][0]["selected_url"] == initial_url
    assert isinstance(created_plan["lines"][0]["selected_unit_price"], str)

    _FakeTrustedPartsClient.offers_by_mpn = {
        "PH4-IC": [_offer("PH4-IC", distributor="DigiKey", stock=50, unit_price=0.75)]
    }
    refreshed = _refresh(authed_client, created_plan["id"])
    assert refreshed.status_code == 200, refreshed.text
    plan = refreshed.json()["data"]
    assert plan["status"] == "refreshed"
    assert plan["last_refreshed_at"] is not None
    assert plan["lines"][0]["selected_url"] == refreshed_url
    assert plan["lines"][0]["selected_qty"] == 3
    assert isinstance(plan["lines"][0]["selected_unit_price"], str)
    assert isinstance(plan["est_total_cost"], str)
    assert Decimal(plan["lines"][0]["selected_unit_price"]) == Decimal("0.75")
    assert Decimal(plan["est_total_cost"]) == Decimal("2.25")

    stock_before = _stock_snapshot(db, workspace_id)
    lots_before = _lot_snapshot(db, workspace_id)

    converted = _convert(authed_client, plan["id"])
    assert converted.status_code == 200, converted.text
    orders = converted.json()["data"]["orders"]
    assert len(orders) == 1
    order = orders[0]
    assert order["supplier"] == "DigiKey"
    assert order["status"] == "draft"
    assert order["currency"] == "EUR"
    assert "TrustedParts purchase plan" in order["comments"]
    assert "distributor=DigiKey" in order["comments"]
    assert f"strategy={plan['strategy']}" in order["comments"]
    assert refreshed_url not in order["comments"]

    assert len(order["entries"]) == 1
    entry = order["entries"][0]
    assert entry["part_id"] == part_id
    assert entry["quantity_ordered"] == 3
    assert isinstance(entry["unit_price"], str)
    assert Decimal(entry["unit_price"]) == Decimal("0.750000")
    assert entry["currency"] == "EUR"
    assert "TrustedParts: distributor=DigiKey" in entry["comments"]
    assert f"plan={plan['id'][:8]}" in entry["comments"]
    assert refreshed_url not in entry["comments"]

    persisted_comments = [
        *(db.execute(select(Order.comments)).scalars().all()),
        *(db.execute(select(OrderEntry.comments)).scalars().all()),
    ]
    assert all(refreshed_url not in (comment or "") for comment in persisted_comments)
    assert _stock_snapshot(db, workspace_id) == stock_before
    assert _lot_snapshot(db, workspace_id) == lots_before

    db_plan = db.get(PurchasePlan, uuid.UUID(plan["id"]))
    assert db_plan is not None
    assert db_plan.status == "converted"


@pytest.mark.parametrize(
    "strategy",
    [
        "lowest_total_price",
        "fewest_distributors",
        "fastest_availability",
        "preferred_first",
    ],
)
def test_each_strategy_produces_a_complete_pipeline(authed_client, strategy: str):
    plan, _part_id = _single_line_refreshed_plan(
        authed_client,
        mpn=f"PH4-{strategy.replace('_', '-')}",
        strategy=strategy,
    )

    converted = _convert(authed_client, plan["id"])

    assert converted.status_code == 200, converted.text
    orders = converted.json()["data"]["orders"]
    assert len(orders) == 1
    assert orders[0]["status"] == "draft"
    assert orders[0]["entries"][0]["quantity_ordered"] == plan["lines"][0]["selected_qty"]


def test_orders_comments_never_contain_selected_url(authed_client, db):
    plan, _part_id = _single_line_refreshed_plan(
        authed_client,
        mpn="PH4-URL",
        distributor="DigiKey",
        unit_price=1.5,
    )
    raw_urls = {line["selected_url"] for line in plan["lines"] if line["selected_url"]}
    assert raw_urls

    converted = _convert(authed_client, plan["id"])

    assert converted.status_code == 200, converted.text
    persisted_comments = [
        *(db.execute(select(Order.comments)).scalars().all()),
        *(db.execute(select(OrderEntry.comments)).scalars().all()),
    ]
    for raw_url in raw_urls:
        assert all(raw_url not in (comment or "") for comment in persisted_comments)


def test_orders_comments_contain_compliance_summary(authed_client):
    plan, _part_id = _single_line_refreshed_plan(authed_client, mpn="PH4-SUMMARY")

    converted = _convert(authed_client, plan["id"])

    assert converted.status_code == 200, converted.text
    order = converted.json()["data"]["orders"][0]
    entry = order["entries"][0]
    assert "TrustedParts purchase plan" in order["comments"]
    assert "distributor=DigiKey" in order["comments"]
    assert f"strategy={plan['strategy']}" in order["comments"]
    assert "TrustedParts: distributor=DigiKey" in entry["comments"]
    assert f"plan={plan['id'][:8]}" in entry["comments"]


def test_ledger_unchanged_at_conversion(authed_client, db):
    plan, _part_id = _single_line_refreshed_plan(authed_client, mpn="PH4-LEDGER")
    workspace_id = _workspace_id(authed_client)
    stock_before = _stock_snapshot(db, workspace_id)
    lots_before = _lot_snapshot(db, workspace_id)

    converted = _convert(authed_client, plan["id"])

    assert converted.status_code == 200, converted.text
    assert _stock_snapshot(db, workspace_id) == stock_before
    assert _lot_snapshot(db, workspace_id) == lots_before


def test_partial_pipeline_failure_rolls_back(authed_client, db, monkeypatch):
    plan = _two_distributor_refreshed_plan(authed_client, prefix="PH4-ROLLBACK")
    from app.domain.sourcing import service as sourcing_service

    original = sourcing_service._create_order_for_distributor
    calls = 0

    def flaky_create(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("boom")
        return original(*args, **kwargs)

    monkeypatch.setattr(
        "app.domain.sourcing.service._create_order_for_distributor",
        flaky_create,
    )

    with pytest.raises(RuntimeError, match="boom"):
        _convert(authed_client, plan["id"])

    workspace_id = _workspace_id(authed_client)
    order_count = db.execute(
        select(func.count()).select_from(Order).where(Order.workspace_id == workspace_id)
    ).scalar_one()
    entry_count = db.execute(
        select(func.count()).select_from(OrderEntry).where(OrderEntry.workspace_id == workspace_id)
    ).scalar_one()
    assert order_count == 0
    assert entry_count == 0
    db_plan = db.get(PurchasePlan, uuid.UUID(plan["id"]))
    assert db_plan is not None
    assert db_plan.status == "refreshed"


def test_unrefreshed_plan_blocks_conversion(authed_client):
    created, _part_id = _single_line_plan(authed_client, mpn="PH4-UNREFRESHED")

    unrefreshed = _convert(authed_client, created["id"])

    assert unrefreshed.status_code == 409, unrefreshed.text
    assert "refresh" in unrefreshed.json()["status"]["message"]


def test_stale_refresh_blocks_conversion(authed_client, db):
    created, _part_id = _single_line_plan(authed_client, mpn="PH4-STALE")
    refreshed = _refresh(authed_client, created["id"])
    assert refreshed.status_code == 200, refreshed.text
    db_plan = db.get(PurchasePlan, uuid.UUID(refreshed.json()["data"]["id"]))
    assert db_plan is not None
    db_plan.last_refreshed_at = utcnow() - timedelta(minutes=11)
    db.flush()

    stale = _convert(authed_client, refreshed.json()["data"]["id"])
    assert stale.status_code == 409, stale.text
    assert stale.json()["status"]["message"] == (
        "plan refresh is stale; refresh again before conversion"
    )


def test_workspace_isolation_full_pipeline(db):
    client_a = _signup_client("phase4-a")
    client_b = _signup_client("phase4-b")
    _configure_sourcing(client_a)
    _configure_sourcing(client_b)
    part_a = create_part(client_a, name="Workspace A part", mpn="PH4-WS-A")
    part_b = create_part(client_b, name="Workspace B part", mpn="PH4-WS-B")
    project_a = create_project_with_bom(
        client_a,
        "Workspace A BOM",
        [{"part_id": part_a, "quantity": 4}],
    )
    project_b = create_project_with_bom(
        client_b,
        "Workspace B BOM",
        [{"part_id": part_b, "quantity": 6}],
    )
    _FakeTrustedPartsClient.offers_by_mpn = {
        "PH4-WS-A": [_offer("PH4-WS-A", distributor="DigiKey", stock=20, unit_price=1.0)],
        "PH4-WS-B": [_offer("PH4-WS-B", distributor="Mouser", stock=20, unit_price=2.0)],
    }
    plan_a = _post_plan(client_a, project_a).json()["data"]
    plan_b = _post_plan(client_b, project_b).json()["data"]
    ws_a = _workspace_id(client_a)
    ws_b = _workspace_id(client_b)

    assert _refresh(client_b, plan_a["id"]).status_code == 404
    assert _convert(client_b, plan_a["id"]).status_code == 404

    refreshed_b = _refresh(client_b, plan_b["id"])
    assert refreshed_b.status_code == 200, refreshed_b.text
    converted_b = _convert(client_b, plan_b["id"])
    assert converted_b.status_code == 200, converted_b.text
    assert converted_b.json()["data"]["orders"][0]["supplier"] == "Mouser"

    order_ids_by_workspace = {
        workspace_id: order_id
        for workspace_id, order_id in db.execute(
            select(Order.workspace_id, Order.id).order_by(Order.workspace_id)
        ).all()
    }
    assert ws_a not in order_ids_by_workspace
    assert ws_b in order_ids_by_workspace


def test_decimal_serialisation_roundtrip(authed_client):
    plan, _part_id = _single_line_refreshed_plan(
        authed_client,
        mpn="PH4-DECIMAL",
        unit_price=1.25,
    )

    converted = _convert(authed_client, plan["id"])

    assert converted.status_code == 200, converted.text
    refreshed_unit = plan["lines"][0]["selected_unit_price"]
    refreshed_total = plan["est_total_cost"]
    order_unit = converted.json()["data"]["orders"][0]["entries"][0]["unit_price"]
    assert isinstance(refreshed_unit, str)
    assert isinstance(refreshed_total, str)
    assert isinstance(order_unit, str)
    assert Decimal(refreshed_unit) == Decimal("1.25")
    assert Decimal(refreshed_total) == Decimal("6.25")
    assert Decimal(order_unit) == Decimal("1.250000")
