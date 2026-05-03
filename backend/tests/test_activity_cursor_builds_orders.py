"""Regression test for issue #279.

`builds.py` and `orders.py` both used `datetime.fromisoformat(...)` in their
activity-cursor endpoints without importing `datetime`. The first request
with a `?before_occurred_at=…` parameter would 500 with NameError. These
tests exercise the cursor-parsing path end-to-end so the missing import is
caught at runtime.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests._factories import (
    create_part as _create_part,
    create_project_with_bom as _create_project_with_bom,
    create_storage as _create_storage,
    signup_user,
)


@pytest.fixture
def authed():
    c = TestClient(app)
    signup_user(c)
    return c


def test_build_activity_cursor_param_accepted(authed):
    """GET /api/builds/{id}/activity with a valid cursor returns 200, not 500."""
    c = authed
    p1 = _create_part(c, "R1k cursor")
    storage = _create_storage(c, "S-cursor-build")
    # Need a project with a BOM to create a build.
    project_id = _create_project_with_bom(
        c, "PCB-cursor", [{"part_id": p1, "quantity": 1}]
    )
    r = c.post(
        "/api/builds",
        json={"name": "B-cursor", "project_id": project_id, "quantity": 1},
    )
    assert r.status_code == 201, r.text
    bid = r.json()["data"]["id"]

    # Pass a valid cursor — without the datetime import this 500'd.
    r = c.get(
        f"/api/builds/{bid}/activity",
        params={
            "before_occurred_at": "2020-01-01T00:00:00",
            "before_id": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert "events" in body
    assert isinstance(body["events"], list)


def test_build_activity_invalid_cursor_returns_422(authed):
    """A malformed before_occurred_at returns 422, not 500."""
    c = authed
    p1 = _create_part(c, "R1k cursor-bad")
    project_id = _create_project_with_bom(
        c, "PCB-cursor-bad", [{"part_id": p1, "quantity": 1}]
    )
    r = c.post(
        "/api/builds",
        json={"name": "B-cursor-bad", "project_id": project_id, "quantity": 1},
    )
    assert r.status_code == 201, r.text
    bid = r.json()["data"]["id"]

    r = c.get(
        f"/api/builds/{bid}/activity",
        params={"before_occurred_at": "not-a-date"},
    )
    assert r.status_code == 422


def test_order_activity_cursor_param_accepted(authed):
    """GET /api/orders/{id}/activity with a valid cursor returns 200, not 500."""
    c = authed
    part_id = _create_part(c, "Cap cursor")
    r = c.post(
        "/api/orders",
        json={
            "name": "PO-cursor",
            "currency": "USD",
            "entries": [
                {"part_id": part_id, "quantity_ordered": 1, "unit_price": "0.05"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    oid = r.json()["data"]["id"]

    # Pass a valid cursor — without the datetime import this 500'd.
    r = c.get(
        f"/api/orders/{oid}/activity",
        params={
            "before_occurred_at": "2020-01-01T00:00:00",
            "before_id": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert "events" in body
    assert isinstance(body["events"], list)


def test_order_activity_invalid_cursor_returns_422(authed):
    """A malformed before_occurred_at returns 422, not 500."""
    c = authed
    part_id = _create_part(c, "Cap cursor-bad")
    r = c.post(
        "/api/orders",
        json={
            "name": "PO-cursor-bad",
            "currency": "USD",
            "entries": [
                {"part_id": part_id, "quantity_ordered": 1, "unit_price": "0.05"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    oid = r.json()["data"]["id"]

    r = c.get(
        f"/api/orders/{oid}/activity",
        params={"before_occurred_at": "not-a-date"},
    )
    assert r.status_code == 422
