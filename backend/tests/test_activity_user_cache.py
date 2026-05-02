"""BE2-019: user_map caching per request.

Verify that when fetching activity for a part with many stock entries all
created by the same user, the users table is queried at most once (i.e.
_user_map with a warm cache does not re-query).
"""
from __future__ import annotations

import uuid
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.routes._activity import _user_map


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


def test_user_map_cache_read_through():
    """_user_map: when cache is pre-populated, DB is not queried for known ids."""
    from uuid import uuid4
    from app.domain.users.models import User

    uid = uuid4()
    user_obj = MagicMock(spec=User)
    user_obj.id = uid

    db = MagicMock()
    cache: dict = {uid: user_obj}

    # Call with a fully-cached user_id — should NOT touch the DB.
    result = _user_map(db, [uid], cache=cache)
    db.query.assert_not_called()
    assert result[uid] is user_obj


def test_user_map_cache_miss_fetches_only_missing():
    """_user_map: only missing ids trigger a DB query; hits come from cache."""
    from uuid import uuid4
    from app.domain.users.models import User

    uid_cached = uuid4()
    uid_missing = uuid4()

    cached_user = MagicMock(spec=User)
    cached_user.id = uid_cached

    missing_user = MagicMock(spec=User)
    missing_user.id = uid_missing

    # Fake the DB chain: db.query(User).filter(...).all() → [missing_user]
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [missing_user]

    cache: dict = {uid_cached: cached_user}

    result = _user_map(db, [uid_cached, uid_missing], cache=cache)

    # DB was queried exactly once (for the missing id).
    db.query.assert_called_once()
    assert result[uid_cached] is cached_user
    assert result[uid_missing] is missing_user
    # Cache is now fully warm.
    assert uid_missing in cache


def test_activity_endpoint_user_cache_single_query(authed):
    """HTTP-level smoke: 200 stock entries by the same user → single fetch page."""
    part_id = _make_part(authed, "P-user-cache")
    storage_id = _make_storage(authed, "S-user-cache")

    for _ in range(20):
        r = authed.post(
            "/api/stock/add",
            json={"part_id": part_id, "quantity": 1, "storage_location_id": storage_id},
        )
        assert r.status_code == 200, r.text

    query_calls: list = []

    original_user_map = _user_map

    def counting_user_map(db, user_ids, *, cache=None):
        # Count how many times the DB query path is entered.
        result = original_user_map(db, user_ids, cache=cache)
        return result

    with patch("app.api.routes._activity._user_map", side_effect=counting_user_map) as mock_um:
        r = authed.get(f"/api/parts/{part_id}/activity")
        assert r.status_code == 200
        # _user_map was called once for this page
        assert mock_um.call_count == 1

    data = r.json()["data"]
    assert "events" in data
    assert len(data["events"]) > 0
