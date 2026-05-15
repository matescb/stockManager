from __future__ import annotations

import re
import statistics
import time
import uuid
from unittest.mock import call, patch

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


def test_smtp_failure_does_not_diverge(db):
    known_email = f"known-smtp-{uuid.uuid4().hex[:8]}@example.com"
    unknown_email = f"unknown-smtp-{uuid.uuid4().hex[:8]}@example.com"
    _signup_and_verify(known_email)

    duplicate_failure = RuntimeError("duplicate signup SMTP down")
    verification_failure = RuntimeError("verification SMTP down")
    with (
        patch.object(settings(), "SIGNUP_REQUIRE_EMAIL_VERIFICATION", True),
        patch(
            "app.api.routes.auth.send_account_exists_email",
            side_effect=duplicate_failure,
        ) as account_exists_email,
        patch(
            "app.api.routes.auth.send_verification_email",
            side_effect=verification_failure,
        ) as verification_email,
        patch("sentry_sdk.capture_exception") as capture_exception,
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
    assert capture_exception.call_args_list == [
        call(duplicate_failure),
        call(verification_failure),
    ]


def test_timing_similar_for_known_and_unknown_email(db):
    known_email = f"known-timing-{uuid.uuid4().hex[:8]}@example.com"
    _signup_and_verify(known_email)
    client = TestClient(app)
    samples = 4

    def _delayed_hash(password: str) -> str:
        assert password == PASSWORD
        time.sleep(0.075)
        return "timing-test-password-hash"

    def _timed_signup(email: str) -> tuple[int, dict, float]:
        start = time.perf_counter()
        response = client.post(
            "/api/auth/signup",
            json={"email": email, "name": "Timing", "password": PASSWORD},
        )
        return response.status_code, response.json(), time.perf_counter() - start

    known_times: list[float] = []
    unknown_times: list[float] = []
    with (
        patch.object(settings(), "SIGNUP_REQUIRE_EMAIL_VERIFICATION", True),
        patch("app.api.routes.auth.hash_password", side_effect=_delayed_hash) as hash_password,
        patch("app.api.routes.auth.send_account_exists_email") as account_exists_email,
        patch("app.api.routes.auth.send_verification_email") as verification_email,
    ):
        for _ in range(samples):
            known_status, known_body, known_elapsed = _timed_signup(known_email)
            unknown_status, unknown_body, unknown_elapsed = _timed_signup(
                f"unknown-timing-{uuid.uuid4().hex[:8]}@example.com"
            )

            assert known_status == unknown_status == 202
            assert known_body == unknown_body
            known_times.append(known_elapsed)
            unknown_times.append(unknown_elapsed)

    assert hash_password.call_count == samples * 2
    assert account_exists_email.call_count == samples
    assert verification_email.call_count == samples

    known_mean = statistics.fmean(known_times)
    unknown_mean = statistics.fmean(unknown_times)
    slower_times = known_times if known_mean >= unknown_mean else unknown_times
    tolerance = max(0.050, 2 * statistics.pstdev(slower_times))
    delta = abs(known_mean - unknown_mean)
    assert delta <= tolerance, (
        f"signup timing diverged: known_mean={known_mean:.4f}s "
        f"unknown_mean={unknown_mean:.4f}s tolerance={tolerance:.4f}s"
    )
