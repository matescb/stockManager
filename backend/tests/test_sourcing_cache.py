from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.domain.sourcing.cache import canonical_query_hash, get_or_fetch, sweep_expired
from app.domain.sourcing.models import SourcingCache
from app.domain.users.models import User
from app.domain.workspaces.models import Workspace


def _workspace(db: Session) -> uuid.UUID:
    user = User(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        name="Sourcing Tester",
        password_hash="test",
    )
    db.add(user)
    db.flush()
    workspace = Workspace(
        name=f"ws-{uuid.uuid4().hex[:8]}",
        kind="organization",
        owner_user_id=user.id,
    )
    db.add(workspace)
    db.flush()
    return workspace.id


def _cache_row(
    *,
    workspace_id: uuid.UUID,
    query: dict,
    response: dict,
    fetched_delta: timedelta = timedelta(),
    ttl: timedelta = timedelta(hours=1),
) -> SourcingCache:
    fetched_at = utcnow() + fetched_delta
    return SourcingCache(
        workspace_id=workspace_id,
        query_hash=canonical_query_hash(query),
        query_json=query,
        response_json=response,
        fetched_at=fetched_at,
        expires_at=fetched_at + ttl,
    )


def test_check_constraint_rejects_8_day_ttl(db: Session) -> None:
    workspace_id = _workspace(db)
    fetched_at = utcnow()

    db.add(
        SourcingCache(
            workspace_id=workspace_id,
            query_hash=canonical_query_hash({"mpn": "BAT54C"}),
            query_json={"mpn": "BAT54C"},
            response_json={"offers": []},
            fetched_at=fetched_at,
            expires_at=fetched_at + timedelta(days=8),
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_canonical_query_hash_is_order_insensitive() -> None:
    assert canonical_query_hash({"a": 1, "b": 2}) == canonical_query_hash({"b": 2, "a": 1})


def test_get_or_fetch_hit(db: Session) -> None:
    workspace_id = _workspace(db)
    query = {"mpn": "STM32F103C8T6", "qty": 10}
    calls = 0

    def fetch() -> dict:
        nonlocal calls
        calls += 1
        return {"source": "fresh", "offers": [{"sku": "A"}]}

    first, first_hit = get_or_fetch(
        db,
        workspace_id=workspace_id,
        query=query,
        ttl_seconds=3600,
        fetch_fn=fetch,
    )
    second, second_hit = get_or_fetch(
        db,
        workspace_id=workspace_id,
        query=query,
        ttl_seconds=3600,
        fetch_fn=lambda: pytest.fail("fetch_fn should not run on cache hit"),
    )

    assert first_hit is False
    assert second_hit is True
    assert second == first
    assert calls == 1


def test_get_or_fetch_miss_after_expiry(db: Session) -> None:
    workspace_id = _workspace(db)
    query = {"mpn": "BAV99"}
    first, first_hit = get_or_fetch(
        db,
        workspace_id=workspace_id,
        query=query,
        ttl_seconds=3600,
        fetch_fn=lambda: {"version": 1},
    )
    assert first == {"version": 1}
    assert first_hit is False

    db.execute(
        update(SourcingCache)
        .where(SourcingCache.workspace_id == workspace_id)
        .where(SourcingCache.query_hash == canonical_query_hash(query))
        .values(expires_at=utcnow() - timedelta(minutes=1))
    )

    second, second_hit = get_or_fetch(
        db,
        workspace_id=workspace_id,
        query=query,
        ttl_seconds=3600,
        fetch_fn=lambda: {"version": 2},
    )

    assert second == {"version": 2}
    assert second_hit is False
    row_count = db.execute(
        select(func.count())
        .select_from(SourcingCache)
        .where(SourcingCache.workspace_id == workspace_id)
    ).scalar_one()
    assert row_count == 1


def test_get_or_fetch_caps_ttl_at_7_days(db: Session) -> None:
    workspace_id = _workspace(db)
    query = {"mpn": "MAX232"}

    get_or_fetch(
        db,
        workspace_id=workspace_id,
        query=query,
        ttl_seconds=30 * 86400,
        fetch_fn=lambda: {"offers": [{"sku": "MAX232-SKU"}]},
    )

    row = db.execute(
        select(SourcingCache)
        .where(SourcingCache.workspace_id == workspace_id)
        .where(SourcingCache.query_hash == canonical_query_hash(query))
    ).scalar_one()
    assert row.expires_at <= row.fetched_at + timedelta(days=7)


def test_sweep_expired_only_deletes_expired_rows(db: Session) -> None:
    workspace_id = _workspace(db)
    expired = _cache_row(
        workspace_id=workspace_id,
        query={"mpn": "expired"},
        response={"expired": True},
        fetched_delta=-timedelta(days=1),
        ttl=timedelta(minutes=1),
    )
    active = _cache_row(
        workspace_id=workspace_id,
        query={"mpn": "active"},
        response={"active": True},
        ttl=timedelta(days=1),
    )
    db.add_all([expired, active])
    db.flush()

    assert sweep_expired(db, workspace_id=workspace_id) == 1

    rows = (
        db.execute(
            select(SourcingCache).where(SourcingCache.workspace_id == workspace_id)
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].response_json == {"active": True}


def test_sweep_expired_is_scoped_to_workspace(db: Session) -> None:
    workspace_a = _workspace(db)
    workspace_b = _workspace(db)
    db.add_all(
        [
            _cache_row(
                workspace_id=workspace_a,
                query={"mpn": "expired-a"},
                response={"workspace": "a"},
                fetched_delta=-timedelta(days=1),
                ttl=timedelta(minutes=1),
            ),
            _cache_row(
                workspace_id=workspace_b,
                query={"mpn": "expired-b"},
                response={"workspace": "b"},
                fetched_delta=-timedelta(days=1),
                ttl=timedelta(minutes=1),
            ),
        ]
    )
    db.flush()

    assert sweep_expired(db, workspace_id=workspace_a) == 1

    rows = db.execute(select(SourcingCache)).scalars().all()
    assert len(rows) == 1
    assert rows[0].workspace_id == workspace_b
    assert rows[0].response_json == {"workspace": "b"}


def test_workspace_isolation_same_query_hash(db: Session) -> None:
    workspace_a = _workspace(db)
    workspace_b = _workspace(db)
    query = {"mpn": "shared", "country": "CZ"}

    response_a, hit_a = get_or_fetch(
        db,
        workspace_id=workspace_a,
        query=query,
        ttl_seconds=3600,
        fetch_fn=lambda: {"workspace": "a"},
    )
    response_b, hit_b = get_or_fetch(
        db,
        workspace_id=workspace_b,
        query=query,
        ttl_seconds=3600,
        fetch_fn=lambda: {"workspace": "b"},
    )
    response_b_cached, hit_b_cached = get_or_fetch(
        db,
        workspace_id=workspace_b,
        query=query,
        ttl_seconds=3600,
        fetch_fn=lambda: pytest.fail("workspace B should hit its own row"),
    )

    assert response_a == {"workspace": "a"}
    assert response_b == {"workspace": "b"}
    assert response_b_cached == {"workspace": "b"}
    assert hit_a is False
    assert hit_b is False
    assert hit_b_cached is True
    assert db.execute(select(func.count()).select_from(SourcingCache)).scalar_one() == 2
