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
from app.domain.orders.models import Order, OrderEntry
from app.domain.sourcing.budget import BUDGET
from app.domain.sourcing.models import PurchasePlan
from app.main import app
from tests._factories import create_part, create_project_with_bom, signup_user
from tests.test_purchase_plan_route import (
    _configure_sourcing,
    _FakeTrustedPartsClient,
    _offer,
    _post_plan,
    _single_line_project,
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
    r = client.post(f"/api/sourcing/purchase-plans/{plan_id}/refresh")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _convert(client: TestClient, plan_id: str, payload: dict[str, Any] | None = None):
    if payload is None:
        return client.post(f"/api/sourcing/purchase-plans/{plan_id}/orders")
    return client.post(f"/api/sourcing/purchase-plans/{plan_id}/orders", json=payload)


def _two_line_project(client: TestClient) -> str:
    part_a = create_part(client, name="Plan A", mpn="PLAN-A")
    part_b = create_part(client, name="Plan B", mpn="PLAN-B")
    return create_project_with_bom(
        client,
        "Two-line plan",
        [{"part_id": part_a, "quantity": 5}, {"part_id": part_b, "quantity": 7}],
    )


def _create_refreshed_plan(
    client: TestClient,
    *,
    project_id: str | None = None,
    offers_by_mpn: dict[str, list[Any]] | None = None,
):
    _configure_sourcing(client, preferred=["DigiKey"])
    if project_id is None:
        project_id = _two_line_project(client)
    if offers_by_mpn is not None:
        _FakeTrustedPartsClient.offers_by_mpn = offers_by_mpn
    plan_response = _post_plan(client, project_id)
    assert plan_response.status_code == 200, plan_response.text
    return _refresh(client, plan_response.json()["data"]["id"])


def test_basic_conversion_creates_one_order_per_distributor(authed_client):
    plan = _create_refreshed_plan(
        authed_client,
        offers_by_mpn={
            "PLAN-A": [_offer("PLAN-A", distributor="DigiKey", stock=100, unit_price=1.0)],
            "PLAN-B": [_offer("PLAN-B", distributor="Mouser", stock=100, unit_price=2.0)],
        },
    )

    r = _convert(authed_client, plan["id"])

    assert r.status_code == 200, r.text
    orders = r.json()["data"]["orders"]
    assert [order["supplier"] for order in orders] == ["DigiKey", "Mouser"]
    assert [len(order["entries"]) for order in orders] == [1, 1]
    assert orders[0]["entries"][0]["quantity_ordered"] == 5
    assert orders[1]["entries"][0]["quantity_ordered"] == 7


def test_plan_line_output_includes_cached_offers(authed_client):
    project_id = _single_line_project(authed_client, mpn="OVERRIDE-OFFERS", quantity=4)
    plan = _create_refreshed_plan(
        authed_client,
        project_id=project_id,
        offers_by_mpn={
            "OVERRIDE-OFFERS": [
                _offer("OVERRIDE-OFFERS", distributor="DigiKey", stock=100, unit_price=1.0),
                _offer("OVERRIDE-OFFERS", distributor="Mouser", stock=100, unit_price=1.1),
            ]
        },
    )

    offers = plan["lines"][0]["available_offers"]
    assert [offer["distributor"] for offer in offers] == ["DigiKey", "Mouser"]
    assert offers[0]["url"] == "https://www.trustedparts.com/OVERRIDE-OFFERS/DigiKey"


def test_conversion_override_uses_cached_offer_selection(authed_client, db):
    project_id = _single_line_project(authed_client, mpn="OVERRIDE-OK", quantity=4)
    plan = _create_refreshed_plan(
        authed_client,
        project_id=project_id,
        offers_by_mpn={
            "OVERRIDE-OK": [
                _offer("OVERRIDE-OK", distributor="DigiKey", stock=100, unit_price=1.0),
                _offer("OVERRIDE-OK", distributor="Mouser", stock=100, unit_price=1.1),
            ]
        },
    )
    line = plan["lines"][0]
    assert line["selected_distributor"] == "DigiKey"
    override_url = "https://www.trustedparts.com/OVERRIDE-OK/Mouser"

    r = _convert(
        authed_client,
        plan["id"],
        {
            "overrides": {
                line["id"]: {
                    "selected_distributor": "Mouser",
                    "selected_qty": 4,
                    "selected_unit_price": "1.1",
                    "selected_currency": "EUR",
                }
            }
        },
    )

    assert r.status_code == 200, r.text
    assert "selected_url" not in r.text
    assert override_url not in r.text
    order = r.json()["data"]["orders"][0]
    assert order["supplier"] == "Mouser"
    assert order["entries"][0]["quantity_ordered"] == 4
    assert Decimal(order["entries"][0]["unit_price"]) == Decimal("1.100000")
    persisted_comments = [
        *(db.execute(select(Order.comments)).scalars().all()),
        *(db.execute(select(OrderEntry.comments)).scalars().all()),
    ]
    assert all(override_url not in (comment or "") for comment in persisted_comments)


def test_invalid_conversion_override_persists_no_orders(authed_client, db):
    project_id = _single_line_project(authed_client, mpn="OVERRIDE-BAD", quantity=4)
    plan = _create_refreshed_plan(
        authed_client,
        project_id=project_id,
        offers_by_mpn={
            "OVERRIDE-BAD": [
                _offer("OVERRIDE-BAD", distributor="DigiKey", stock=100, unit_price=1.0),
            ]
        },
    )
    line = plan["lines"][0]

    r = _convert(
        authed_client,
        plan["id"],
        {
            "overrides": {
                line["id"]: {
                    "selected_distributor": "Mouser",
                    "selected_qty": 4,
                    "selected_unit_price": "1.1",
                    "selected_currency": "EUR",
                }
            }
        },
    )

    assert r.status_code == 422, r.text
    assert "cached offers" in r.json()["status"]["message"]
    assert db.execute(select(func.count()).select_from(Order)).scalar_one() == 0
    assert db.execute(select(func.count()).select_from(OrderEntry)).scalar_one() == 0


def test_override_line_from_other_workspace_persists_no_orders(authed_client, db):
    plan_a = _create_refreshed_plan(
        authed_client,
        project_id=_single_line_project(authed_client, mpn="OVERRIDE-WS-A", quantity=4),
        offers_by_mpn={
            "OVERRIDE-WS-A": [
                _offer("OVERRIDE-WS-A", distributor="DigiKey", stock=100, unit_price=1.0)
            ]
        },
    )
    client_b = TestClient(app)
    signup_user(client_b)
    plan_b = _create_refreshed_plan(
        client_b,
        project_id=_single_line_project(client_b, mpn="OVERRIDE-WS-B", quantity=4),
        offers_by_mpn={
            "OVERRIDE-WS-B": [
                _offer("OVERRIDE-WS-B", distributor="Mouser", stock=100, unit_price=2.0)
            ]
        },
    )

    r = _convert(
        client_b,
        plan_b["id"],
        {
            "overrides": {
                plan_a["lines"][0]["id"]: {
                    "selected_distributor": "Mouser",
                    "selected_qty": 4,
                    "selected_unit_price": "2.0",
                    "selected_currency": "EUR",
                }
            }
        },
    )

    assert r.status_code == 422, r.text
    assert "purchase plan" in r.json()["status"]["message"]
    assert db.execute(select(func.count()).select_from(Order)).scalar_one() == 0
    assert db.execute(select(func.count()).select_from(OrderEntry)).scalar_one() == 0


def test_override_mixed_currency_guard_persists_no_orders(authed_client, db):
    plan = _create_refreshed_plan(
        authed_client,
        offers_by_mpn={
            "PLAN-A": [_offer("PLAN-A", distributor="DigiKey", stock=100, unit_price=1.0)],
            "PLAN-B": [
                _offer("PLAN-B", distributor="DigiKey", stock=100, unit_price=2.0),
                _offer(
                    "PLAN-B",
                    distributor="DigiKey",
                    stock=100,
                    unit_price=3.0,
                    currency="USD",
                ),
            ],
        },
    )
    line_b = next(line for line in plan["lines"] if line["mpn_searched"] == "PLAN-B")

    r = _convert(
        authed_client,
        plan["id"],
        {
            "overrides": {
                line_b["id"]: {
                    "selected_distributor": "DigiKey",
                    "selected_qty": 7,
                    "selected_unit_price": "3.0",
                    "selected_currency": "USD",
                }
            }
        },
    )

    assert r.status_code == 422, r.text
    assert "mixed currencies" in r.json()["status"]["message"]
    assert db.execute(select(func.count()).select_from(Order)).scalar_one() == 0
    assert db.execute(select(func.count()).select_from(OrderEntry)).scalar_one() == 0


def test_orders_status_is_draft(authed_client):
    plan = _create_refreshed_plan(authed_client)

    r = _convert(authed_client, plan["id"])

    assert r.status_code == 200, r.text
    assert {order["status"] for order in r.json()["data"]["orders"]} == {"draft"}


def test_order_comments_excludes_raw_url(authed_client, db):
    raw_url = "https://www.trustedparts.com/PLAN-A/DigiKey"
    project_id = _single_line_project(authed_client, mpn="PLAN-A", quantity=3)
    plan = _create_refreshed_plan(
        authed_client,
        project_id=project_id,
        offers_by_mpn={
            "PLAN-A": [_offer("PLAN-A", distributor="DigiKey", stock=100, unit_price=1.0)]
        },
    )
    assert plan["lines"][0]["selected_url"] == raw_url

    r = _convert(authed_client, plan["id"])

    assert r.status_code == 200, r.text
    persisted_comments = [
        *(db.execute(select(Order.comments)).scalars().all()),
        *(db.execute(select(OrderEntry.comments)).scalars().all()),
    ]
    assert all(raw_url not in (comment or "") for comment in persisted_comments)


def test_order_comments_contains_compliance_summary(authed_client):
    plan = _create_refreshed_plan(authed_client)

    r = _convert(authed_client, plan["id"])

    assert r.status_code == 200, r.text
    order = r.json()["data"]["orders"][0]
    entry = order["entries"][0]
    assert "TrustedParts purchase plan" in order["comments"]
    assert f"strategy={plan['strategy']}" in order["comments"]
    assert "TrustedParts: distributor=" in entry["comments"]
    assert f"plan={plan['id'][:8]}" in entry["comments"]


def test_plan_status_flips_to_converted(authed_client, db):
    plan = _create_refreshed_plan(authed_client)

    r = _convert(authed_client, plan["id"])

    assert r.status_code == 200, r.text
    db_plan = db.get(PurchasePlan, uuid.UUID(plan["id"]))
    assert db_plan is not None
    assert db_plan.status == "converted"


def test_unrefreshed_plan_returns_409(authed_client):
    _configure_sourcing(authed_client)
    project_id = _single_line_project(authed_client, mpn="UNREFRESHED")
    r_plan = _post_plan(authed_client, project_id)
    assert r_plan.status_code == 200, r_plan.text

    r = _convert(authed_client, r_plan.json()["data"]["id"])

    assert r.status_code == 409, r.text
    assert "refresh" in r.json()["status"]["message"]


def test_stale_refresh_returns_409(authed_client, db):
    plan = _create_refreshed_plan(authed_client)
    db_plan = db.get(PurchasePlan, uuid.UUID(plan["id"]))
    assert db_plan is not None
    db_plan.last_refreshed_at = utcnow() - timedelta(minutes=11)
    db.flush()

    r = _convert(authed_client, plan["id"])

    assert r.status_code == 409, r.text
    assert r.json()["status"]["message"] == "plan refresh is stale; refresh again before conversion"


def test_mixed_currency_in_group_returns_422(authed_client):
    plan = _create_refreshed_plan(
        authed_client,
        offers_by_mpn={
            "PLAN-A": [
                _offer(
                    "PLAN-A",
                    distributor="DigiKey",
                    stock=100,
                    unit_price=1.0,
                    currency="EUR",
                )
            ],
            "PLAN-B": [
                _offer(
                    "PLAN-B",
                    distributor="DigiKey",
                    stock=100,
                    unit_price=2.0,
                    currency="USD",
                )
            ],
        },
    )

    r = _convert(authed_client, plan["id"])

    assert r.status_code == 422, r.text
    assert "mixed currencies" in r.json()["status"]["message"]


def test_foreign_plan_returns_404(authed_client):
    plan = _create_refreshed_plan(authed_client)
    client_b = TestClient(app)
    signup_user(client_b)
    _configure_sourcing(client_b)

    r = _convert(client_b, plan["id"])

    assert r.status_code == 404, r.text
    assert r.json()["status"]["category"] == "not_found"


def test_partial_failure_rolls_back_all_orders(authed_client, db, monkeypatch):
    plan = _create_refreshed_plan(
        authed_client,
        offers_by_mpn={
            "PLAN-A": [_offer("PLAN-A", distributor="DigiKey", stock=100, unit_price=1.0)],
            "PLAN-B": [_offer("PLAN-B", distributor="Mouser", stock=100, unit_price=2.0)],
        },
    )
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

    assert db.execute(select(func.count()).select_from(Order)).scalar_one() == 0
    assert db.execute(select(func.count()).select_from(OrderEntry)).scalar_one() == 0
    db_plan = db.get(PurchasePlan, uuid.UUID(plan["id"]))
    assert db_plan is not None
    assert db_plan.status == "refreshed"


def test_workspace_isolation_two_plans_different_workspaces(authed_client):
    plan_a = _create_refreshed_plan(authed_client, project_id=None)

    client_b = TestClient(app)
    signup_user(client_b)
    plan_b = _create_refreshed_plan(client_b, project_id=None)

    foreign = _convert(client_b, plan_a["id"])
    own = _convert(client_b, plan_b["id"])

    assert foreign.status_code == 404, foreign.text
    assert own.status_code == 200, own.text
    assert own.json()["data"]["orders"]


def test_decimal_serialisation_roundtrip(authed_client):
    plan = _create_refreshed_plan(
        authed_client,
        offers_by_mpn={
            "PLAN-A": [_offer("PLAN-A", distributor="DigiKey", stock=100, unit_price=1.25)],
            "PLAN-B": [_offer("PLAN-B", distributor="Mouser", stock=100, unit_price=2.5)],
        },
    )

    r = _convert(authed_client, plan["id"])

    assert r.status_code == 200, r.text
    first_price = r.json()["data"]["orders"][0]["entries"][0]["unit_price"]
    assert isinstance(first_price, str)
    assert Decimal(first_price) == Decimal("1.25")
