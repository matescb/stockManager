"""Concurrency regression for build-consume (BE-003 high, TEST-003).

Two builds against the same project; both threads attempt to consume
overlapping BOM lines whose total exceeds on-hand. Exactly one (at
most) must succeed; on-hand must never go negative for any part.

This pins `domain/builds/service.py::consume`'s use of
`lock_parts_for_stock_write`. If the lock is bypassed or moved out
of the surrounding transaction, both threads can pass the per-part
sufficiency check and double-consume — that's the bug.

Pattern copied from `test_stock_concurrency.py
::test_concurrent_removes_cannot_both_drain_below_zero`. Each thread
gets its own TestClient; cookies are copied from the fixture client.
"""
from __future__ import annotations

import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


pytestmark = pytest.mark.real_db


def _signup(c: TestClient) -> None:
    r = c.post(
        "/api/auth/signup",
        json={
            "email": f"u-{uuid.uuid4().hex[:8]}@x.com",
            "name": "u",
            "password": "TestPass-2026-Stronk",
        },
    )
    assert r.status_code == 200, r.text


@pytest.fixture
def authed():
    c = TestClient(app)
    _signup(c)
    return c


def _create_storage(c, name="Bin"):
    r = c.post("/api/storage", json={"name": name})
    assert r.status_code in (200, 201)
    return r.json()["data"]["id"]


def _create_part(c, name="P"):
    r = c.post("/api/parts", json={"name": name, "part_type": "local"})
    assert r.status_code in (200, 201)
    return r.json()["data"]["id"]


def _add_stock(c, part_id, qty, storage_id):
    r = c.post(
        "/api/stock/add",
        json={
            "part_id": part_id,
            "quantity": qty,
            "storage_location_id": storage_id,
        },
    )
    assert r.status_code in (200, 201), r.text


def _create_project_with_bom(c, name, bom):
    r = c.post("/api/projects", json={"name": name})
    assert r.status_code in (200, 201)
    pid = r.json()["data"]["id"]
    for row in bom:
        r = c.post(
            f"/api/projects/{pid}/entries",
            json={
                "part_id": row.get("part_id"),
                "quantity": row["quantity"],
                "dnp": row.get("dnp", False),
                "name": row.get("name"),
            },
        )
        assert r.status_code in (200, 201), r.text
    return pid


def _copy_cookies(src: TestClient, dst: TestClient) -> None:
    for cookie in src.cookies.jar:
        dst.cookies.set(
            cookie.name, cookie.value, domain=cookie.domain, path=cookie.path
        )


def test_concurrent_consume_cannot_drain_below_zero(authed):
    """Two builds, BOM lines `P1=50, P2=50`; on-hand for each is 60.
    Both threads consume in parallel. Exactly one (at most) succeeds;
    on-hand must never go negative."""
    c = authed
    p1 = _create_part(c, "Part-1")
    p2 = _create_part(c, "Part-2")
    storage_id = _create_storage(c, "Shelf")
    _add_stock(c, p1, 60, storage_id)
    _add_stock(c, p2, 60, storage_id)

    proj_id = _create_project_with_bom(
        c,
        f"PRJ-{uuid.uuid4().hex[:6]}",
        [
            {"part_id": p1, "quantity": 50},
            {"part_id": p2, "quantity": 50},
        ],
    )

    # Two builds against the same project; both want to consume 50 of
    # each. 50+50=100 > 60 on-hand for each part, so only one can win.
    r = c.post(
        "/api/builds",
        json={"name": "B-1", "project_id": proj_id, "quantity": 1},
    )
    assert r.status_code == 201, r.text
    b1 = r.json()["data"]["id"]
    r = c.post(
        "/api/builds",
        json={"name": "B-2", "project_id": proj_id, "quantity": 1},
    )
    assert r.status_code == 201, r.text
    b2 = r.json()["data"]["id"]

    entries = c.get(f"/api/projects/{proj_id}/entries").json()["data"]
    e_p1 = next(e for e in entries if e["part_id"] == p1)
    e_p2 = next(e for e in entries if e["part_id"] == p2)

    results: list[int] = []
    barrier = threading.Barrier(2)

    def do_consume(build_id: str) -> None:
        cli = TestClient(app)
        _copy_cookies(authed, cli)
        barrier.wait()
        r = cli.post(
            f"/api/builds/{build_id}/consume",
            json={
                "lines": [
                    {
                        "project_entry_id": e_p1["id"],
                        "part_id": p1,
                        "quantity": 50,
                        "storage_location_id": storage_id,
                    },
                    {
                        "project_entry_id": e_p2["id"],
                        "part_id": p2,
                        "quantity": 50,
                        "storage_location_id": storage_id,
                    },
                ]
            },
        )
        results.append(r.status_code)

    t1 = threading.Thread(target=do_consume, args=(b1,))
    t2 = threading.Thread(target=do_consume, args=(b2,))
    t1.start()
    t2.start()
    t1.join(timeout=20)
    t2.join(timeout=20)

    assert len(results) == 2
    successes = sum(1 for s in results if s in (200, 201))
    assert successes <= 1, f"both consumes succeeded: {results}"

    # Post-condition: on-hand never goes negative for either part.
    for pid in (p1, p2):
        stock = authed.get(f"/api/parts/{pid}/stock").json()["data"]
        total = stock["total_on_hand"]
        assert total >= 0, f"part {pid} went negative: {total}; results={results}"
        # Either 60 (no consume succeeded) or 10 (one succeeded)
        assert total in (10, 60), (
            f"unexpected total {total} for {pid}; results={results}"
        )
