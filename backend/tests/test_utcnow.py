"""Pin the shared time utility (CQ-001 / issue #117).

`app.core.time.utcnow` is the one place we ask the system clock for
"now" — every model column default and every service-layer timestamp
goes through it. This test asserts the basic contract and pins the
SQLAlchemy `default=` wiring so a future fork (re-introducing a local
`_utcnow`) breaks the test before it ships.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.core.time import utcnow
from app.domain._mixins import WorkspaceOwned
from app.domain.stock.models import StockEntry
from app.domain.users.models import User, UserSession
from app.domain.workspaces.models import (
    Workspace,
    WorkspaceInvitation,
    WorkspaceMember,
)


def test_utcnow_returns_tz_aware_utc_datetime():
    now = utcnow()
    assert isinstance(now, datetime)
    assert now.tzinfo is not None
    assert now.utcoffset() == timezone.utc.utcoffset(now)


def test_utcnow_is_non_decreasing():
    a = utcnow()
    b = utcnow()
    assert b >= a


def _is_shared_utcnow(default_arg) -> bool:
    """Identity-or-by-name match. SQLAlchemy may copy a mixin's
    `Column` per-subclass and the copy can hold a separate function
    reference (depending on declarative internals); compare on
    qualified name so the test pins "the shared utcnow" without
    depending on the exact module-import identity."""
    if default_arg is utcnow:
        return True
    return (
        getattr(default_arg, "__module__", None) == "app.core.time"
        and getattr(default_arg, "__qualname__", None) == "utcnow"
    )


def test_models_use_shared_utcnow_default():
    """The same callable backs every Column(default=...) — pin it so a
    future refactor can't silently re-fork the time helper."""
    columns = [
        WorkspaceOwned.created_at,
        WorkspaceOwned.updated_at,
        StockEntry.occurred_at,
        StockEntry.created_at,
        User.created_at,
        UserSession.created_at,
        UserSession.last_used_at,
        Workspace.created_at,
        WorkspaceMember.created_at,
        WorkspaceInvitation.created_at,
    ]
    for col in columns:
        assert col.default is not None, f"{col} has no default"
        assert _is_shared_utcnow(col.default.arg), (
            f"{col} default is {col.default.arg!r}, expected app.core.time.utcnow"
        )

    # onupdate path on the audited mixin
    assert WorkspaceOwned.updated_at.onupdate is not None
    assert _is_shared_utcnow(WorkspaceOwned.updated_at.onupdate.arg)
