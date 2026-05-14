from __future__ import annotations

import re
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

PASSWORD = "StrongVerify-2026!!"


def _signup_and_verify(email: str) -> None:
    client = TestClient(app)
    captured: dict[str, str] = {}

    def _capture_link(*, to: str, verification_link: str) -> None:
        assert to == email
        captured["link"] = verification_link

    with (
        patch.object(settings(), "SIGNUP_REQUIRE_EMAIL_VERIFICATION", True),
        patch("app.api.routes.auth.send_verification_email", side_effect=_capture_link),
    ):
        response = client.post(
            "/api/auth/signup",
            json={"email": email, "name": "Known", "password": PASSWORD},
        )
    assert response.status_code == 202, response.text

    match_id = re.search(r"[?&]id=([^&]+)", captured["link"])
    match_token = re.search(r"[?&]token=([^&]+)", captured["link"])
    assert match_id and match_token

    response = client.post(
        "/api/auth/verify",
        json={"id": match_id.group(1), "token": match_token.group(1)},
    )
    assert response.status_code == 200, response.text


def test_response_identical_for_known_and_unknown_email(db):
    known_email = f"known-{uuid.uuid4().hex[:8]}@example.com"
    unknown_email = f"unknown-{uuid.uuid4().hex[:8]}@example.com"
    _signup_and_verify(known_email)

    with (
        patch.object(settings(), "SIGNUP_REQUIRE_EMAIL_VERIFICATION", True),
        patch("app.api.routes.auth.send_account_exists_email") as account_exists_email,
        patch("app.api.routes.auth.send_verification_email") as verification_email,
    ):
        known_response = TestClient(app).post(
            "/api/auth/signup",
            json={"email": known_email, "name": "Known Again", "password": PASSWORD},
        )
        unknown_response = TestClient(app).post(
            "/api/auth/signup",
            json={"email": unknown_email, "name": "Unknown", "password": PASSWORD},
        )

    assert known_response.status_code == unknown_response.status_code == 202
    assert known_response.json() == unknown_response.json()
    account_exists_email.assert_called_once_with(to=known_email)
    verification_email.assert_called_once()
