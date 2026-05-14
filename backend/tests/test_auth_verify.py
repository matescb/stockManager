from __future__ import annotations

import re
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.routes import auth as auth_routes
from app.core.config import settings
from app.domain.users.models import User, UserSession
from app.main import app

PASSWORD = "StrongVerify-2026!!"


def _signup_for_verification(c: TestClient, email: str) -> tuple[str, str]:
    captured: dict[str, str] = {}

    def _cap(*, to: str, verification_link: str) -> None:
        captured["link"] = verification_link

    with (
        patch.object(settings(), "SIGNUP_REQUIRE_EMAIL_VERIFICATION", True),
        patch("app.api.routes.auth.send_verification_email", side_effect=_cap),
    ):
        response = c.post(
            "/api/auth/signup",
            json={"email": email, "name": "Verifier", "password": PASSWORD},
        )
    assert response.status_code == 202, response.text

    pending_id = re.search(r"[?&]id=([^&]+)", captured["link"])
    token = re.search(r"[?&]token=([^&]+)", captured["link"])
    assert pending_id and token
    return pending_id.group(1), token.group(1)


def test_verify_rotates_sessions(db, monkeypatch):
    c = TestClient(app)
    email = f"v-{uuid.uuid4().hex[:8]}@x.com"
    pending_id, token = _signup_for_verification(c, email)

    calls: list[str] = []
    real_revoke = auth_routes.revoke_all_user_sessions
    real_create = auth_routes.create_session_row

    def _revoke_all_user_sessions(spy_db, user_id):
        calls.append("revoke")
        return real_revoke(spy_db, user_id)

    def _create_session_row(spy_db, user_id):
        calls.append("create")
        return real_create(spy_db, user_id)

    monkeypatch.setattr(auth_routes, "revoke_all_user_sessions", _revoke_all_user_sessions)
    monkeypatch.setattr(auth_routes, "create_session_row", _create_session_row)

    response = c.post("/api/auth/verify", json={"id": pending_id, "token": token})
    assert response.status_code == 200, response.text
    assert calls == ["revoke", "create"]

    user = db.query(User).filter(User.email == email).one()
    assert db.query(UserSession).filter(UserSession.user_id == user.id).count() == 1
