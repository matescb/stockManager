from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.secrets import decrypt
from app.core.time import utcnow
from app.domain.projects.models import Project
from app.domain.sourcing.cache import (
    canonical_query_hash,
    get_or_fetch,
    sourcing_search_query,
    sweep_expired,
    sweep_expired_all_workspaces,
)
from app.domain.sourcing.models import PurchasePlan, SourcingCache
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


def _current_workspace_id(authed_client) -> uuid.UUID:
    response = authed_client.get("/api/workspaces/current")
    assert response.status_code == 200, response.text
    return uuid.UUID(response.json()["data"]["id"])


def _project(db: Session, *, workspace_id: uuid.UUID) -> Project:
    project = Project(
        workspace_id=workspace_id,
        name=f"optimizer-project-{uuid.uuid4().hex[:8]}",
    )
    db.add(project)
    db.flush()
    return project


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


def _sourcing_query(
    *,
    workspace_id: uuid.UUID,
    provider: str = "trustedparts",
    mpn: str = "BAT54C",
) -> dict:
    return sourcing_search_query(
        workspace_id=workspace_id,
        provider=provider,
        mpn=mpn,
        country_code="CZ",
        currency_code="EUR",
        language_code="en",
        distributors=["DigiKey", "Mouser"],
        in_stock_only=False,
        use_cached_data=True,
    )


def _cache_count(db: Session, *, workspace_id: uuid.UUID, provider: str) -> int:
    return db.execute(
        select(func.count())
        .select_from(SourcingCache)
        .where(SourcingCache.workspace_id == workspace_id)
        .where(SourcingCache.query_json["provider"].astext == provider)
    ).scalar_one()


def _purchase_plan(
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    created_delta: timedelta = timedelta(),
    ttl: timedelta = timedelta(days=1),
) -> PurchasePlan:
    created_at = utcnow() + created_delta
    return PurchasePlan(
        workspace_id=workspace_id,
        project_id=project_id,
        build_quantity=1,
        strategy="lowest_total_price",
        status="draft",
        created_at=created_at,
        expires_at=created_at + ttl,
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


def test_canonical_query_includes_country_currency_language_distributors() -> None:
    base = sourcing_search_query(
        workspace_id=uuid.uuid4(),
        provider="TrustedParts",
        mpn=" BAT54C ",
        country_code="cz",
        currency_code="eur",
        language_code="EN",
        distributors=["mouser", "DigiKey"],
        in_stock_only=False,
        use_cached_data=True,
    )

    assert set(base) == {
        "workspace_id",
        "provider",
        "mpn",
        "country_code",
        "currency_code",
        "language_code",
        "distributors",
        "in_stock_only",
        "use_cached_data",
        "exact_match",
    }
    assert base["provider"] == "trustedparts"
    assert base["mpn"] == "BAT54C"
    assert base["country_code"] == "CZ"
    assert base["currency_code"] == "EUR"
    assert base["language_code"] == "en"
    assert base["distributors"] == ["DigiKey", "Mouser"]

    changed_inputs = [
        {"workspace_id": uuid.uuid4()},
        {"provider": "mouser"},
        {"mpn": "BAV99"},
        {"country_code": "DE"},
        {"currency_code": "USD"},
        {"language_code": "de"},
        {"distributors": ["DigiKey"]},
    ]
    for change in changed_inputs:
        params = {
            "workspace_id": uuid.UUID(base["workspace_id"]),
            "provider": base["provider"],
            "mpn": base["mpn"],
            "country_code": base["country_code"],
            "currency_code": base["currency_code"],
            "language_code": base["language_code"],
            "distributors": base["distributors"],
            "in_stock_only": base["in_stock_only"],
            "use_cached_data": base["use_cached_data"],
            **change,
        }
        candidate = sourcing_search_query(**params)
        assert canonical_query_hash(candidate) != canonical_query_hash(base)


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


def test_rotate_api_key_purges_cache_rows(authed_client, db: Session) -> None:
    workspace_id = _current_workspace_id(authed_client)
    other_workspace_id = _workspace(db)
    rows = [
        _cache_row(
            workspace_id=workspace_id,
            query=_sourcing_query(workspace_id=workspace_id, provider="trustedparts"),
            response={"provider": "trustedparts"},
        ),
        _cache_row(
            workspace_id=workspace_id,
            query=_sourcing_query(
                workspace_id=workspace_id,
                provider="mouser",
                mpn="MOU-1",
            ),
            response={"provider": "mouser"},
        ),
        _cache_row(
            workspace_id=other_workspace_id,
            query=_sourcing_query(workspace_id=other_workspace_id, provider="trustedparts"),
            response={"provider": "trustedparts", "workspace": "other"},
        ),
    ]
    db.add_all(rows)
    db.flush()

    response = authed_client.patch(
        "/api/workspaces/current",
        json={
            "sourcing_provider": "trustedparts",
            "sourcing_api_key": "rotated-api-key",
        },
    )

    assert response.status_code == 200, response.text
    assert _cache_count(db, workspace_id=workspace_id, provider="trustedparts") == 0
    assert _cache_count(db, workspace_id=workspace_id, provider="mouser") == 1
    assert _cache_count(db, workspace_id=other_workspace_id, provider="trustedparts") == 1


def test_delete_api_key_purges_cache_rows(authed_client, db: Session) -> None:
    workspace_id = _current_workspace_id(authed_client)
    response = authed_client.patch(
        "/api/workspaces/current",
        json={
            "sourcing_provider": "trustedparts",
            "sourcing_api_key": "api-key-before-delete",
        },
    )
    assert response.status_code == 200, response.text
    db.add(
        _cache_row(
            workspace_id=workspace_id,
            query=_sourcing_query(workspace_id=workspace_id, provider="trustedparts"),
            response={"provider": "trustedparts"},
        )
    )
    db.flush()

    response = authed_client.patch(
        "/api/workspaces/current",
        json={"sourcing_api_key": ""},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["has_sourcing_api_key"] is False
    assert _cache_count(db, workspace_id=workspace_id, provider="trustedparts") == 0


def test_rotate_failure_does_not_partial_commit(
    authed_client,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = _current_workspace_id(authed_client)
    response = authed_client.patch(
        "/api/workspaces/current",
        json={
            "sourcing_provider": "trustedparts",
            "sourcing_api_key": "old-api-key",
        },
    )
    assert response.status_code == 200, response.text

    def fail_purge(*args, **kwargs) -> int:
        raise RuntimeError("purge failed")

    monkeypatch.setattr(
        "app.api.routes.workspaces.sourcing_cache.purge_provider_cache",
        fail_purge,
    )
    with pytest.raises(RuntimeError, match="purge failed"):
        authed_client.patch(
            "/api/workspaces/current",
            json={"sourcing_api_key": "new-api-key"},
        )

    db.expire_all()
    workspace = db.get(Workspace, workspace_id)
    assert workspace is not None
    assert decrypt(workspace.sourcing_api_key_enc) == "old-api-key"


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
        db.execute(select(SourcingCache).where(SourcingCache.workspace_id == workspace_id))
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


def test_sweep_expired_all_workspaces_preserves_unexpired_rows(db: Session) -> None:
    workspace_a = _workspace(db)
    workspace_b = _workspace(db)
    db.add_all(
        [
            _cache_row(
                workspace_id=workspace_a,
                query={"mpn": "expired-a"},
                response={"workspace": "a", "expired": True},
                fetched_delta=-timedelta(days=1),
                ttl=timedelta(minutes=1),
            ),
            _cache_row(
                workspace_id=workspace_a,
                query={"mpn": "active-a"},
                response={"workspace": "a", "active": True},
                ttl=timedelta(days=1),
            ),
            _cache_row(
                workspace_id=workspace_b,
                query={"mpn": "expired-b"},
                response={"workspace": "b", "expired": True},
                fetched_delta=-timedelta(days=1),
                ttl=timedelta(minutes=1),
            ),
            _cache_row(
                workspace_id=workspace_b,
                query={"mpn": "active-b"},
                response={"workspace": "b", "active": True},
                ttl=timedelta(days=1),
            ),
        ]
    )
    db.flush()

    assert sweep_expired_all_workspaces(db) == 2

    rows = db.execute(select(SourcingCache).order_by(SourcingCache.workspace_id)).scalars().all()
    assert len(rows) == 2
    assert {row.workspace_id for row in rows} == {workspace_a, workspace_b}
    assert {row.response_json["active"] for row in rows} == {True}


def test_sweeper_also_deletes_expired_purchase_plans(db: Session) -> None:
    workspace_id = _workspace(db)
    project = _project(db, workspace_id=workspace_id)
    expired = _purchase_plan(
        workspace_id=workspace_id,
        project_id=project.id,
        created_delta=-timedelta(days=1),
        ttl=timedelta(minutes=1),
    )
    active = _purchase_plan(
        workspace_id=workspace_id,
        project_id=project.id,
        ttl=timedelta(days=1),
    )
    db.add_all([expired, active])
    db.flush()

    assert sweep_expired_all_workspaces(db) == 1

    rows = db.execute(select(PurchasePlan)).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == active.id


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
