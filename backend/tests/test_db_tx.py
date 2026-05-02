"""Transaction-boundary tests for the `get_db` dep (BE2-010).

The dep yields the session, then COMMITs on a clean route exit and
ROLLs BACK on a raised exception. Routes must not call `db.commit()`
themselves anymore — the dep owns the boundary. This file pins both
ends:

  - happy path: a write-then-200 route persists without the route
    calling commit.
  - error path: a write-then-raise route's writes are rolled back, even
    though we wrote and flushed first.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.core.deps import CurrentWorkspace, DbSession
from app.core.responses import ok
from app.domain.parts.models import Part
from app.main import app


# Mount a couple of test-only routes that exercise the dep transaction
# behaviour directly. We attach to the running `app` because that's the
# only place where `Depends(require_member_for_writes)` and the rest of
# the membership chain are wired — we want the FULL request shape, not
# a synthetic Session call.
_router = APIRouter()


@_router.post("/__test__/db-tx/happy/{name}")
def _happy_path(name: str, db: DbSession, ws: CurrentWorkspace):
    """Write a Part — let the dep commit on clean exit."""
    p = Part(workspace_id=ws.id, part_type="local", name=name)
    db.add(p)
    db.flush()
    return ok({"id": str(p.id)})


@_router.post("/__test__/db-tx/raise/{name}")
def _error_path(name: str, db: DbSession, ws: CurrentWorkspace):
    """Write a Part, flush so the row is in the session, THEN raise.
    The dep must rollback — so a follow-up GET must not see the row."""
    p = Part(workspace_id=ws.id, part_type="local", name=name)
    db.add(p)
    db.flush()
    raise HTTPException(status_code=500, detail="forced raise after write")


# Wire the helper router only the first time the module is imported.
# pytest may import this file multiple times across xdist workers, so
# guard against re-mounting.
if not getattr(app.state, "_test_db_tx_mounted", False):
    app.include_router(_router, prefix="/api")
    app.state._test_db_tx_mounted = True


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
def client():
    c = TestClient(app)
    _signup(c)
    return c


def test_dep_commits_on_clean_exit(client):
    name = f"happy-{uuid.uuid4().hex[:8]}"
    r = client.post(f"/api/__test__/db-tx/happy/{name}")
    assert r.status_code == 200, r.text
    # The list endpoint runs in a fresh request → fresh session. If the
    # dep didn't commit, this read would not see the write.
    found = client.get("/api/parts").json()["data"]
    assert any(p["name"] == name for p in found), f"part {name} not committed"


def test_dep_rolls_back_on_raise(client):
    name = f"raise-{uuid.uuid4().hex[:8]}"
    r = client.post(f"/api/__test__/db-tx/raise/{name}")
    assert r.status_code == 500
    # The row was added + flushed before the raise. The dep must have
    # rolled back — so a fresh-session list does NOT see it.
    found = client.get("/api/parts").json()["data"]
    assert not any(p["name"] == name for p in found), (
        f"part {name} leaked through rollback"
    )
