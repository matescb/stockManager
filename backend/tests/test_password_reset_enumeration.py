from __future__ import annotations

import statistics
import time
import uuid
from unittest.mock import patch

from app.domain.users.models import PasswordResetRequest
from tests._factories import signup_user


def test_no_enumeration_on_request(client, db):
    known_email = f"known-reset-{uuid.uuid4().hex[:8]}@example.com"
    unknown_email = f"unknown-reset-{uuid.uuid4().hex[:8]}@example.com"
    signup_user(client, email=known_email)

    with (
        patch("app.api.routes.auth.send_password_reset_email") as send_mail,
        patch("app.api.routes.auth.hash_password", return_value="dummy-hash") as hash_password,
    ):
        known_response = client.post(
            "/api/auth/request-password-reset",
            json={"email": known_email},
        )
        unknown_response = client.post(
            "/api/auth/request-password-reset",
            json={"email": unknown_email},
        )

    assert known_response.status_code == unknown_response.status_code == 202
    assert known_response.json() == unknown_response.json()
    assert send_mail.call_count == 1
    assert hash_password.call_count == 2
    assert db.query(PasswordResetRequest).count() == 1


def test_no_enumeration_timing_parity(client):
    known_email = f"known-reset-timing-{uuid.uuid4().hex[:8]}@example.com"
    signup_user(client, email=known_email)
    samples = 4

    def _delayed_hash(password: str) -> str:
        assert password
        time.sleep(0.05)
        return "dummy-hash"

    def _timed_request(email: str) -> tuple[int, dict, float]:
        start = time.perf_counter()
        response = client.post(
            "/api/auth/request-password-reset",
            json={"email": email},
        )
        return response.status_code, response.json(), time.perf_counter() - start

    known_times: list[float] = []
    unknown_times: list[float] = []
    with (
        patch("app.api.routes.auth.hash_password", side_effect=_delayed_hash),
        patch("app.api.routes.auth.send_password_reset_email"),
    ):
        for _ in range(samples):
            known_status, known_body, known_elapsed = _timed_request(known_email)
            unknown_status, unknown_body, unknown_elapsed = _timed_request(
                f"unknown-reset-timing-{uuid.uuid4().hex[:8]}@example.com"
            )

            assert known_status == unknown_status == 202
            assert known_body == unknown_body
            known_times.append(known_elapsed)
            unknown_times.append(unknown_elapsed)

    known_mean = statistics.fmean(known_times)
    unknown_mean = statistics.fmean(unknown_times)
    slower_times = known_times if known_mean >= unknown_mean else unknown_times
    tolerance = max(0.050, 2 * statistics.pstdev(slower_times))
    delta = abs(known_mean - unknown_mean)
    assert delta <= tolerance, (
        f"password reset timing diverged: known_mean={known_mean:.4f}s "
        f"unknown_mean={unknown_mean:.4f}s tolerance={tolerance:.4f}s"
    )
