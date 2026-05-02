"""HIBP k-anonymity password check tests (SEC2-014).

Covers:
- A suffix found in the HIBP response rejects the password.
- A network error / timeout causes the check to fail-open (not reject).
- The breach-count logic (any suffix match with count > 0 rejects).
"""
from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch

import pytest

from app.core.auth import WeakPasswordError, _hibp_check


_PASSWORD = "SecureButInBreachList!99"


def _sha1_parts(password: str) -> tuple[str, str]:
    """Return (prefix, suffix) of the SHA-1 hex digest (uppercase)."""
    digest = hashlib.sha1(password.encode()).hexdigest().upper()  # noqa: S324
    return digest[:5], digest[5:]


def test_hibp_rejects_breached_password():
    """When the HIBP range response contains the password suffix with count > 0,
    WeakPasswordError is raised."""
    prefix, suffix = _sha1_parts(_PASSWORD)
    # Build a fake response body containing the suffix with a nonzero count.
    fake_body = f"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA:1\n{suffix}:42\nBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB:100\n"

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = fake_body

    with patch("httpx.get", return_value=mock_resp):
        with pytest.raises(WeakPasswordError, match="known data breaches"):
            _hibp_check(_PASSWORD)


def test_hibp_accepts_clean_password():
    """When the HIBP range response does NOT contain the password suffix,
    no exception is raised."""
    prefix, suffix = _sha1_parts(_PASSWORD)
    # Response does not include our suffix.
    fake_body = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA:1\nBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB:100\n"

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = fake_body

    with patch("httpx.get", return_value=mock_resp):
        _hibp_check(_PASSWORD)  # Should not raise.


def test_hibp_fail_open_on_network_error():
    """A network error / timeout causes the check to fail-open.

    HIBP fail-open is intentional (see implementation plan): blocking ALL
    signups when HIBP is down is worse than the brief signal loss.
    """
    with patch("httpx.get", side_effect=TimeoutError("simulated timeout")):
        _hibp_check(_PASSWORD)  # Should NOT raise.


def test_hibp_fail_open_on_http_error():
    """A non-2xx HTTP response from HIBP also fails-open."""
    import httpx

    with patch("httpx.get", side_effect=httpx.HTTPStatusError("500", request=MagicMock(), response=MagicMock())):
        _hibp_check(_PASSWORD)  # Should NOT raise.


def test_hibp_suffix_match_zero_count_accepts():
    """A suffix in the HIBP response with count = 0 should NOT reject the password.

    HIBP's padding feature (Add-Padding=true) includes random fictitious
    suffixes with count 0. These must be ignored.
    """
    prefix, suffix = _sha1_parts(_PASSWORD)
    fake_body = f"{suffix}:0\nAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA:5\n"

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = fake_body

    with patch("httpx.get", return_value=mock_resp):
        _hibp_check(_PASSWORD)  # Should NOT raise — count is 0.
