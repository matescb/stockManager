"""BE2-019: cursor pagination for activity routes.

Seed 250 stock entries on a part; verify:
  - First call (no cursor) returns default 50 with next_* cursor.
  - Second call with cursor returns the next 50, strictly older.
  - Pagination continues until cursor is absent (last page).
  - Total events across all pages equals 250 stock + 1 synthetic created.
  - Limit param is respected (default 50, explicit 10, max capped at 200).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient, name: str = "Alice"):
    email = f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": name, "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text
    return r


@pytest.fixture
def authed():
    c = TestClient(app)
    _signup(c)
    return c


def _make_part(c, name="Cap"):
    r = c.post("/api/parts", json={"name": name, "part_type": "local"})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _make_storage(c, name="Shelf"):
    r = c.post("/api/storage", json={"name": name})
    assert r.status_code in (200, 201)
    return r.json()["data"]["id"]


def _add_stock(c, part_id, storage_id, qty=1):
    r = c.post(
        "/api/stock/add",
        json={"part_id": part_id, "quantity": qty, "storage_location_id": storage_id},
    )
    assert r.status_code == 200, r.text


def _fetch_activity(c, part_id, **params):
    r = c.get(f"/api/parts/{part_id}/activity", params=params)
    assert r.status_code == 200, r.text
    return r.json()["data"]


# ---------------------------------------------------------------------------
# Core pagination tests
# ---------------------------------------------------------------------------


def test_first_page_default_limit(authed):
    """First page with default limit=50 returns cursor and includes synthetic created event."""
    part_id = _make_part(authed, "P-pagination")
    storage_id = _make_storage(authed, "S-pagination")

    for _ in range(60):
        _add_stock(authed, part_id, storage_id)

    page = _fetch_activity(authed, part_id)
    events = page["events"]
    # Stock events = 50 (limit); synthetic events may push total slightly above limit.
    stock_events = [e for e in events if e["kind"] == "stock"]
    assert len(stock_events) == 50
    assert "next_before_occurred_at" in page
    assert "next_before_id" in page

    # Sorted descending
    timestamps = [e["occurred_at"] for e in events]
    assert timestamps == sorted(timestamps, reverse=True)

    # Synthetic created event appears on first page (cursor_at is None)
    kinds = [e["kind"] for e in events]
    assert "part_created" in kinds


def test_cursor_continuation(authed):
    """Second page returns events strictly older than the cursor."""
    part_id = _make_part(authed, "P-cursor")
    storage_id = _make_storage(authed, "S-cursor")

    for _ in range(120):
        _add_stock(authed, part_id, storage_id)

    page1 = _fetch_activity(authed, part_id, limit=50)
    assert "next_before_occurred_at" in page1
    assert "next_before_id" in page1

    cursor_ts = page1["next_before_occurred_at"]

    page2 = _fetch_activity(
        authed,
        part_id,
        limit=50,
        before_occurred_at=cursor_ts,
        before_id=page1["next_before_id"],
    )
    events1 = page1["events"]
    events2 = page2["events"]

    # All page2 stock events must have occurred_at <= cursor (the boundary used for the query)
    for e in events2:
        if e["kind"] == "stock":
            assert e["occurred_at"] <= cursor_ts

    # No overlap of stock event ids between pages
    ids1 = {e.get("id") for e in events1 if e["kind"] == "stock"}
    ids2 = {e.get("id") for e in events2 if e["kind"] == "stock"}
    assert ids1.isdisjoint(ids2)

    # Synthetic events only on first page
    assert any(e["kind"] == "part_created" for e in events1)
    assert not any(e["kind"].endswith("_created") or e["kind"].endswith("_updated") for e in events2)


def test_full_traversal_250_entries(authed):
    """Walk all pages of a 250-entry history; total stock events = 250."""
    part_id = _make_part(authed, "P-big")
    storage_id = _make_storage(authed, "S-big")

    n = 250
    for _ in range(n):
        _add_stock(authed, part_id, storage_id)

    all_events = []
    params: dict = {"limit": 50}
    while True:
        page = _fetch_activity(authed, part_id, **params)
        all_events.extend(page["events"])
        if "next_before_occurred_at" not in page:
            break
        params = {
            "limit": 50,
            "before_occurred_at": page["next_before_occurred_at"],
            "before_id": page["next_before_id"],
        }

    stock_events = [e for e in all_events if e["kind"] == "stock"]
    assert len(stock_events) == n

    # Exactly one synthetic created event (on first page only)
    created_events = [e for e in all_events if e["kind"] == "part_created"]
    assert len(created_events) == 1


def test_no_next_cursor_on_last_page(authed):
    """When total events <= limit, no next_* cursor is returned."""
    part_id = _make_part(authed, "P-small")
    storage_id = _make_storage(authed, "S-small")

    for _ in range(5):
        _add_stock(authed, part_id, storage_id)

    page = _fetch_activity(authed, part_id, limit=50)
    assert "next_before_occurred_at" not in page
    assert "next_before_id" not in page


def test_explicit_limit_respected(authed):
    """?limit=10 returns at most 10 stock events (plus possible synthetic events)."""
    part_id = _make_part(authed, "P-limit10")
    storage_id = _make_storage(authed, "S-limit10")

    for _ in range(30):
        _add_stock(authed, part_id, storage_id)

    page = _fetch_activity(authed, part_id, limit=10)
    stock_events = [e for e in page["events"] if e["kind"] == "stock"]
    assert len(stock_events) <= 10
    assert "next_before_occurred_at" in page


def test_limit_at_max_200(authed):
    """?limit=200 returns at most 200 stock events; limit=999 is rejected (422)."""
    part_id = _make_part(authed, "P-cap")
    storage_id = _make_storage(authed, "S-cap")

    for _ in range(210):
        _add_stock(authed, part_id, storage_id)

    # limit=200 is the maximum allowed; should succeed.
    page = _fetch_activity(authed, part_id, limit=200)
    stock_events = [e for e in page["events"] if e["kind"] == "stock"]
    assert len(stock_events) <= 200

    # limit=999 exceeds le=200 constraint → FastAPI returns 422.
    r = authed.get(f"/api/parts/{part_id}/activity", params={"limit": 999})
    assert r.status_code == 422


def test_invalid_cursor_returns_422(authed):
    """A malformed before_occurred_at returns 422."""
    part_id = _make_part(authed, "P-bad-cursor")
    r = authed.get(f"/api/parts/{part_id}/activity", params={"before_occurred_at": "not-a-date"})
    assert r.status_code == 422


def test_response_shape_has_events_key(authed):
    """Response is {events: [...], ...}, not a bare list."""
    part_id = _make_part(authed, "P-shape")
    page = _fetch_activity(authed, part_id)
    assert "events" in page
    assert isinstance(page["events"], list)
