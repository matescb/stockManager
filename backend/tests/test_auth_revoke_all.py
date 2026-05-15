from __future__ import annotations

import uuid

from app.core.auth import create_session_row, revoke_all_user_sessions
from app.domain.users.models import User, UserSession


def test_revoke_all_commits_explicitly(db):
    user = User(
        email=f"revoke-all-{uuid.uuid4().hex[:8]}@example.com",
        name="Revoke All",
        password_hash="not-used",
    )
    db.add(user)
    db.flush()

    create_session_row(db, user.id)
    create_session_row(db, user.id)
    db.commit()
    assert db.query(UserSession).filter(UserSession.user_id == user.id).count() == 2

    revoked = revoke_all_user_sessions(db, user.id)
    assert revoked == 2

    try:
        raise RuntimeError("simulate later caller failure")
    except RuntimeError:
        db.rollback()

    db.expire_all()
    assert db.query(UserSession).filter(UserSession.user_id == user.id).count() == 0
