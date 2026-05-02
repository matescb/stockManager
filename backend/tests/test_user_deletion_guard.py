"""Regression for DB-013 / issue #104.

`workspaces.owner_user_id` carries `ondelete='RESTRICT'`. Today there is
no `DELETE /api/users/{id}` endpoint, but the guard helper in
`app.domain.users.service.assert_user_deletable` is the user-friendly
layer above the FK: any future delete path must call it first so the
caller sees a structured 409 with the list of owned workspaces instead
of a Postgres `ForeignKeyViolation` bubbling to a 500.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.domain.users.models import User
from app.domain.users.service import assert_user_deletable
from app.domain.workspaces.models import Workspace, WorkspaceMember


def _make_user(db, email_suffix: str = "") -> User:
    suffix = email_suffix or uuid.uuid4().hex[:8]
    u = User(
        email=f"u-{suffix}@example.com",
        name="Tester",
        password_hash="x" * 60,
    )
    db.add(u)
    db.flush()
    return u


def _make_workspace(db, owner: User, name: str = "WS") -> Workspace:
    ws = Workspace(name=name, kind="organization", owner_user_id=owner.id)
    db.add(ws)
    db.flush()
    return ws


def test_assert_user_deletable_ok_when_no_owned_workspaces(db):
    """User who owns nothing passes the guard cleanly — no exception."""
    u = _make_user(db)
    db.commit()

    # Must return None, not raise.
    result = assert_user_deletable(db, u.id)
    assert result is None


def test_assert_user_deletable_raises_409_for_workspace_owner(db):
    """Owning a single workspace surfaces a structured 409 detail."""
    owner = _make_user(db)
    ws = _make_workspace(db, owner, name="Acme")
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        assert_user_deletable(db, owner.id)

    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail.get("code") == "owns_workspaces"
    assert detail.get("message") == "user owns workspaces"
    workspaces = detail.get("workspaces") or []
    assert len(workspaces) == 1
    assert workspaces[0]["id"] == str(ws.id)
    assert workspaces[0]["name"] == "Acme"


def test_assert_user_deletable_lists_every_owned_workspace(db):
    """Multi-workspace ownership returns all of them in 409 detail."""
    owner = _make_user(db)
    ws1 = _make_workspace(db, owner, name="One")
    ws2 = _make_workspace(db, owner, name="Two")
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        assert_user_deletable(db, owner.id)

    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    ids = {w["id"] for w in detail["workspaces"]}
    assert ids == {str(ws1.id), str(ws2.id)}


def test_assert_user_deletable_ignores_membership_in_others_workspaces(db):
    """A user who is only a *member* (not owner) of someone else's
    workspace passes the guard. The check is strictly on
    `owner_user_id`, not membership."""
    owner = _make_user(db, "owner")
    member_only = _make_user(db, "member")
    ws = _make_workspace(db, owner, name="Owned by other")
    db.add(
        WorkspaceMember(
            workspace_id=ws.id, user_id=member_only.id, role="member", status="active"
        )
    )
    db.commit()

    # The member-only user owns nothing → guard passes.
    assert assert_user_deletable(db, member_only.id) is None
