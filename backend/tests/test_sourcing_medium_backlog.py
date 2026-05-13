from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.domain.sourcing import service as sourcing_service
from app.domain.sourcing.budget import BudgetTracker
from app.domain.sourcing.constants import INFINITE_LEAD_TIME_DAYS
from app.domain.sourcing.schemas import (
    SourcingBomLineOut,
    SourcingBomOfferOut,
    SourcingBomPriceBreakOut,
    SourcingDistributor,
    SourcingPriceBreak,
)


def _workspace(workspace_id):
    return SimpleNamespace(id=workspace_id)


def _workspace_object(workspace_id):
    return SimpleNamespace(workspace_id=workspace_id)


def test_service_helpers_reject_preloaded_objects_from_other_workspace():
    workspace = _workspace(uuid4())
    foreign = _workspace_object(uuid4())

    with pytest.raises(HTTPException) as source_exc:
        sourcing_service.source_bom(
            None,
            workspace=workspace,
            project=foreign,
            build_quantity=1,
        )
    assert source_exc.value.status_code == 404

    with pytest.raises(HTTPException) as build_exc:
        sourcing_service.build_purchase_plan(
            None,
            workspace=workspace,
            project=foreign,
            build_quantity=1,
        )
    assert build_exc.value.status_code == 404

    with pytest.raises(HTTPException) as refresh_exc:
        sourcing_service.refresh_purchase_plan(
            None,
            workspace=workspace,
            plan=foreign,
        )
    assert refresh_exc.value.status_code == 404

    with pytest.raises(HTTPException) as convert_exc:
        sourcing_service.convert_plan_to_orders(
            None,
            workspace=workspace,
            plan=foreign,
            user_id=None,
        )
    assert convert_exc.value.status_code == 404


def test_best_offer_uses_shortage_quantity_price_breaks():
    bulk = SourcingBomOfferOut(
        mpn="BULK",
        distributor="Bulk",
        stock=100,
        unit_price=Decimal("1.00"),
        price_breaks=[
            SourcingBomPriceBreakOut(quantity=1, unit_price=Decimal("1.00")),
            SourcingBomPriceBreakOut(quantity=10, unit_price=Decimal("0.80")),
        ],
    )
    eaches = SourcingBomOfferOut(
        mpn="BULK",
        distributor="Eaches",
        stock=100,
        unit_price=Decimal("0.90"),
        price_breaks=[
            SourcingBomPriceBreakOut(quantity=1, unit_price=Decimal("0.90")),
        ],
    )

    assert sourcing_service._best_offer_at_qty([bulk, eaches], 10) is bulk


def test_bom_row_extended_cost_uses_converted_best_offer(monkeypatch):
    offer = SourcingBomOfferOut(
        mpn="FX",
        distributor="DigiKey",
        stock=100,
        unit_price=Decimal("2.00"),
        currency="USD",
        price_breaks=[
            SourcingBomPriceBreakOut(quantity=1, unit_price=Decimal("2.00")),
        ],
    )
    row = SourcingBomLineOut(
        project_entry_id=uuid4(),
        part_id=uuid4(),
        part_name="FX Line",
        required=5,
        available=0,
        substitute_available=0,
        short_by=5,
        authorized_stock=100,
        offers=[offer],
        best_offer=offer,
        est_extended_cost=Decimal("10.00"),
    )
    monkeypatch.setattr(
        sourcing_service.fx_rates,
        "get_or_fetch_today",
        lambda _db, *, on_date: {"EUR": Decimal("1"), "USD": Decimal("2")},
    )

    assert (
        sourcing_service._apply_fx_to_bom_rows(None, [row], requested_currency="EUR")
        == "ok"
    )

    assert row.best_offer is not None
    assert row.best_offer.unit_price_converted == Decimal("1.0000")
    assert row.est_extended_cost == Decimal("5.0000")


def test_bom_row_extended_cost_is_not_recomputed_without_fx():
    offer = SourcingBomOfferOut(
        mpn="NOFX",
        distributor="DigiKey",
        stock=100,
        unit_price=Decimal("2.00"),
        currency="USD",
        price_breaks=[
            SourcingBomPriceBreakOut(quantity=1, unit_price=Decimal("2.00")),
            SourcingBomPriceBreakOut(quantity=5, unit_price=Decimal("1.00")),
        ],
    )
    row = SourcingBomLineOut(
        project_entry_id=uuid4(),
        part_id=uuid4(),
        part_name="No FX Line",
        required=5,
        available=0,
        substitute_available=0,
        short_by=5,
        authorized_stock=100,
        offers=[offer],
        best_offer=offer,
        est_extended_cost=Decimal("10.00"),
    )

    assert sourcing_service._apply_fx_to_bom_rows(None, [row], requested_currency=None) is None
    assert row.est_extended_cost == Decimal("10.00")


def test_offer_extended_cost_preserves_zero_shortage_and_quantizes_products():
    offer = SourcingBomOfferOut(
        mpn="QTY",
        distributor="DigiKey",
        stock=100,
        unit_price=Decimal("0.333333"),
        currency="USD",
        price_breaks=[
            SourcingBomPriceBreakOut(quantity=1, unit_price=Decimal("0.333333")),
        ],
    )

    assert sourcing_service._offer_extended_cost(offer, 0) == Decimal("0")
    assert sourcing_service._offer_extended_cost(offer, 3) == Decimal("1.0000")


def test_infinite_lead_time_sentinel_is_not_valid_wire_output():
    with pytest.raises(ValueError):
        SourcingBomOfferOut(
            mpn="SENTINEL",
            distributor="DigiKey",
            stock=1,
            lead_time_days=INFINITE_LEAD_TIME_DAYS,
        )


def test_wire_prices_parse_as_decimal_without_float_round_trip():
    distributor = SourcingDistributor(
        name="DigiKey",
        unit_price=0.1,
        price_breaks=[SourcingPriceBreak(quantity=1, unit_price=0.1)],
    )

    assert distributor.unit_price == Decimal("0.1")
    assert distributor.price_breaks[0].unit_price == Decimal("0.1")


def test_budget_tracker_records_concurrent_updates():
    tracker = BudgetTracker()
    workspace_id = uuid4()

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _index: tracker.record(workspace_id, 1), range(40)))

    assert tracker._window_total(workspace_id, 10) == 40
