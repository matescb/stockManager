"""BE2-026 — lot/storage history endpoints enforce limit <= 1000.

Seeding 250 stock rows on a single lot/storage and verifying:
- default request returns 200 rows (not all 250)
- ?limit=50 returns 50 rows
- ?limit=2000 returns 422 (le=1000 constraint)
- workspace-isolation: foreign lot/storage id returns 404 before limit fires
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from tests._factories import signup_user, create_part, create_storage, add_stock, DEFAULT_PASSWORD


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _remove(client: TestClient, part_id: str, qty: int, storage_id: str | None = None, lot_id: str | None = None) -> None:
    body: dict = {"part_id": part_id, "quantity": qty}
    if storage_id:
        body["storage_location_id"] = storage_id
    if lot_id:
        body["lot_id"] = lot_id
    r = client.post("/api/stock/remove", json=body)
    assert r.status_code == 200, r.text


def _seed_lot_history(client: TestClient, n_adjusts: int) -> tuple[str, str]:
    """Return (part_id, lot_id) after seeding n_adjusts adjust-count entries.

    Strategy:
    1. One add_stock call creates the lot (entry #1).
    2. n_adjusts calls to /lots/{lot_id}/adjust-count alternate between 5 and 4,
       each producing one stock entry on the lot.
    Total entries >= n_adjusts (the initial add + adjust entries).
    """
    part_id = create_part(client, name=f"HistLotPart-{uuid.uuid4().hex[:6]}")
    # First add creates the lot; capture the lot_id from the response
    r = add_stock(client, part_id, qty=10, lot_name="seed-lot")
    lot_id = r.json()["data"]["lot_id"]

    for i in range(n_adjusts):
        # Alternate actual_quantity so we always create a non-zero delta
        actual = 5 if i % 2 == 0 else 10
        r2 = client.post(f"/api/lots/{lot_id}/adjust-count", json={"actual_quantity": actual})
        assert r2.status_code == 200, r2.text

    return part_id, lot_id


def _seed_storage_history(client: TestClient, n_pairs: int) -> tuple[str, str]:
    """Return (part_id, storage_id) after seeding n_pairs add+remove entries."""
    part_id = create_part(client, name=f"HistStorPart-{uuid.uuid4().hex[:6]}")
    storage_id = create_storage(client, name=f"HistBin-{uuid.uuid4().hex[:6]}")
    for _ in range(n_pairs):
        add_stock(client, part_id, qty=2, storage_id=storage_id)
        _remove(client, part_id, qty=1, storage_id=storage_id)
    return part_id, storage_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def c():
    from app.main import app
    client = TestClient(app)
    signup_user(client)
    return client


@pytest.fixture
def c2():
    """A second, independent workspace."""
    from app.main import app
    client = TestClient(app)
    signup_user(client, email=f"u2-{uuid.uuid4().hex[:8]}@example.com")
    return client


# ---------------------------------------------------------------------------
# Tests: lot history
# ---------------------------------------------------------------------------

def test_lot_history_default_limit(c: TestClient):
    """Default returns exactly 200 rows when 250+ are stored."""
    # 250 adjusts + 1 initial add = 251 entries total
    part_id, lot_id = _seed_lot_history(c, n_adjusts=250)
    r = c.get(f"/api/lots/{lot_id}/history")
    assert r.status_code == 200
    rows = r.json()["data"]
    assert len(rows) == 200


def test_lot_history_custom_limit(c: TestClient):
    """?limit=50 returns at most 50 rows."""
    part_id, lot_id = _seed_lot_history(c, n_adjusts=60)
    r = c.get(f"/api/lots/{lot_id}/history?limit=50")
    assert r.status_code == 200
    rows = r.json()["data"]
    assert len(rows) == 50


def test_lot_history_limit_too_large(c: TestClient):
    """?limit=2000 (>1000) must return 422 Unprocessable Entity."""
    # Use a dummy UUID — validation fires before the DB lookup
    fake_id = str(uuid.uuid4())
    r = c.get(f"/api/lots/{fake_id}/history?limit=2000")
    assert r.status_code == 422


def test_lot_history_foreign_workspace_404(c: TestClient, c2: TestClient):
    """A lot from workspace-A is not visible to workspace-B (isolation check)."""
    part_id, lot_id = _seed_lot_history(c, n_adjusts=3)
    r = c2.get(f"/api/lots/{lot_id}/history?limit=200")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Tests: storage history
# ---------------------------------------------------------------------------

def test_storage_history_default_limit(c: TestClient):
    """Default returns exactly 200 rows when 250+ are stored."""
    # 125 pairs → 250 entries (add + remove each)
    _part_id, storage_id = _seed_storage_history(c, n_pairs=125)
    r = c.get(f"/api/storage/{storage_id}/history")
    assert r.status_code == 200
    rows = r.json()["data"]
    assert len(rows) == 200


def test_storage_history_custom_limit(c: TestClient):
    """?limit=50 returns exactly 50 rows when more exist."""
    _part_id, storage_id = _seed_storage_history(c, n_pairs=60)
    r = c.get(f"/api/storage/{storage_id}/history?limit=50")
    assert r.status_code == 200
    rows = r.json()["data"]
    assert len(rows) == 50


def test_storage_history_limit_too_large(c: TestClient):
    """?limit=2000 (>1000) must return 422 Unprocessable Entity."""
    fake_id = str(uuid.uuid4())
    r = c.get(f"/api/storage/{fake_id}/history?limit=2000")
    assert r.status_code == 422


def test_storage_history_foreign_workspace_404(c: TestClient, c2: TestClient):
    """A storage from workspace-A is not visible to workspace-B."""
    _part_id, storage_id = _seed_storage_history(c, n_pairs=5)
    r = c2.get(f"/api/storage/{storage_id}/history?limit=200")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Helpers for cursor-pagination tests
# ---------------------------------------------------------------------------

def _walk_lot_history_pages(client: TestClient, lot_id: str, page_size: int) -> list[dict]:
    """Walk all cursor pages for a lot history and return all items."""
    all_items: list[dict] = []
    cursor: str | None = None
    while True:
        url = f"/api/lots/{lot_id}/history?paged=true&limit={page_size}"
        if cursor:
            url += f"&cursor={cursor}"
        r = client.get(url)
        assert r.status_code == 200, r.text
        body = r.json()["data"]
        all_items.extend(body["items"])
        cursor = body["next_cursor"]
        if not cursor:
            break
    return all_items


def _walk_storage_history_pages(client: TestClient, storage_id: str, page_size: int) -> list[dict]:
    """Walk all cursor pages for a storage history and return all items."""
    all_items: list[dict] = []
    cursor: str | None = None
    while True:
        url = f"/api/storage/{storage_id}/history?paged=true&limit={page_size}"
        if cursor:
            url += f"&cursor={cursor}"
        r = client.get(url)
        assert r.status_code == 200, r.text
        body = r.json()["data"]
        all_items.extend(body["items"])
        cursor = body["next_cursor"]
        if not cursor:
            break
    return all_items


# ---------------------------------------------------------------------------
# Cursor-pagination tests: lot history
# ---------------------------------------------------------------------------

def test_lot_history_paged_round_trip(c: TestClient):
    """Seed 250 entries, walk cursor to exhaustion; verify no duplicates and all rows visited."""
    _part_id, lot_id = _seed_lot_history(c, n_adjusts=250)
    # 250 adjusts + 1 initial add = 251 entries total
    all_items = _walk_lot_history_pages(c, lot_id, page_size=100)
    ids = [item["id"] for item in all_items]
    assert len(ids) == len(set(ids)), "Duplicate entry ids found across pages"
    assert len(ids) == 251


def test_lot_history_paged_monotonic_desc(c: TestClient):
    """occurred_at values across pages are monotonically non-increasing (DESC order)."""
    _part_id, lot_id = _seed_lot_history(c, n_adjusts=60)
    all_items = _walk_lot_history_pages(c, lot_id, page_size=25)
    timestamps = [item["occurred_at"] for item in all_items]
    for i in range(1, len(timestamps)):
        assert timestamps[i - 1] >= timestamps[i], (
            f"Out of DESC order at index {i}: {timestamps[i - 1]!r} < {timestamps[i]!r}"
        )


def test_lot_history_paged_last_page_no_cursor(c: TestClient):
    """The final page must have next_cursor=None."""
    _part_id, lot_id = _seed_lot_history(c, n_adjusts=10)
    # 11 entries total; use page_size=6 → 2 pages (6 + 5)
    cursor: str | None = None
    last_next_cursor = "sentinel"
    while True:
        url = f"/api/lots/{lot_id}/history?paged=true&limit=6"
        if cursor:
            url += f"&cursor={cursor}"
        r = c.get(url)
        assert r.status_code == 200
        body = r.json()["data"]
        last_next_cursor = body["next_cursor"]
        cursor = last_next_cursor
        if not cursor:
            break
    assert last_next_cursor is None


def test_lot_history_cursor_tamper_400(c: TestClient):
    """A tampered cursor token must return HTTP 400."""
    _part_id, lot_id = _seed_lot_history(c, n_adjusts=60)
    r = c.get(f"/api/lots/{lot_id}/history?paged=true&limit=25")
    assert r.status_code == 200
    real_cursor = r.json()["data"]["next_cursor"]
    assert real_cursor is not None, "Expected a next_cursor with 61 entries and limit=25"

    # Tamper — flip last character.
    chars = list(real_cursor)
    chars[-1] = "X" if chars[-1] != "X" else "Y"
    bad_cursor = "".join(chars)

    r2 = c.get(f"/api/lots/{lot_id}/history?paged=true&limit=25&cursor={bad_cursor}")
    assert r2.status_code == 400, r2.text


def test_lot_history_cross_workspace_404_before_400(c: TestClient, c2: TestClient):
    """Workspace B cannot access workspace A's lot — returns 404 regardless of cursor."""
    _part_id, lot_id = _seed_lot_history(c, n_adjusts=60)
    # Get a valid cursor from workspace A.
    r = c.get(f"/api/lots/{lot_id}/history?paged=true&limit=25")
    assert r.status_code == 200
    cursor_a = r.json()["data"]["next_cursor"]
    assert cursor_a is not None

    # Workspace B tries to access workspace A's lot (with or without cursor).
    r2 = c2.get(f"/api/lots/{lot_id}/history?paged=true&limit=25&cursor={cursor_a}")
    assert r2.status_code == 404, r2.text


# ---------------------------------------------------------------------------
# Cursor-pagination tests: storage history
# ---------------------------------------------------------------------------

def test_storage_history_paged_round_trip(c: TestClient):
    """Seed 250 entries (125 pairs), walk cursor to exhaustion; verify no duplicates."""
    _part_id, storage_id = _seed_storage_history(c, n_pairs=125)
    all_items = _walk_storage_history_pages(c, storage_id, page_size=100)
    ids = [item["id"] for item in all_items]
    assert len(ids) == len(set(ids)), "Duplicate entry ids found across pages"
    assert len(ids) == 250


def test_storage_history_paged_monotonic_desc(c: TestClient):
    """occurred_at values across pages are monotonically non-increasing (DESC order)."""
    _part_id, storage_id = _seed_storage_history(c, n_pairs=35)
    all_items = _walk_storage_history_pages(c, storage_id, page_size=25)
    timestamps = [item["occurred_at"] for item in all_items]
    for i in range(1, len(timestamps)):
        assert timestamps[i - 1] >= timestamps[i], (
            f"Out of DESC order at index {i}: {timestamps[i - 1]!r} < {timestamps[i]!r}"
        )


def test_storage_history_paged_last_page_no_cursor(c: TestClient):
    """The final page must have next_cursor=None."""
    _part_id, storage_id = _seed_storage_history(c, n_pairs=6)
    # 12 entries total; use page_size=7 → 2 pages (7 + 5)
    cursor: str | None = None
    last_next_cursor = "sentinel"
    while True:
        url = f"/api/storage/{storage_id}/history?paged=true&limit=7"
        if cursor:
            url += f"&cursor={cursor}"
        r = c.get(url)
        assert r.status_code == 200
        body = r.json()["data"]
        last_next_cursor = body["next_cursor"]
        cursor = last_next_cursor
        if not cursor:
            break
    assert last_next_cursor is None


def test_storage_history_cursor_tamper_400(c: TestClient):
    """A tampered cursor token must return HTTP 400."""
    _part_id, storage_id = _seed_storage_history(c, n_pairs=35)
    r = c.get(f"/api/storage/{storage_id}/history?paged=true&limit=25")
    assert r.status_code == 200
    real_cursor = r.json()["data"]["next_cursor"]
    assert real_cursor is not None, "Expected a next_cursor with 70 entries and limit=25"

    chars = list(real_cursor)
    chars[-1] = "X" if chars[-1] != "X" else "Y"
    bad_cursor = "".join(chars)

    r2 = c.get(f"/api/storage/{storage_id}/history?paged=true&limit=25&cursor={bad_cursor}")
    assert r2.status_code == 400, r2.text


def test_storage_history_cross_workspace_404_before_400(c: TestClient, c2: TestClient):
    """Workspace B cannot access workspace A's storage — returns 404 regardless of cursor."""
    _part_id, storage_id = _seed_storage_history(c, n_pairs=35)
    # Get a valid cursor from workspace A.
    r = c.get(f"/api/storage/{storage_id}/history?paged=true&limit=25")
    assert r.status_code == 200
    cursor_a = r.json()["data"]["next_cursor"]
    assert cursor_a is not None

    # Workspace B tries to access workspace A's storage.
    r2 = c2.get(f"/api/storage/{storage_id}/history?paged=true&limit=25&cursor={cursor_a}")
    assert r2.status_code == 404, r2.text
