"""Prod safety for the email-verification mail backend (issue #281).

Pins:
  - Settings refuses to construct in prod when any required SMTP / mail
    var is missing (`_require_smtp_in_prod` validator).
  - Settings constructs cleanly in prod when all required values are
    populated.
  - Dev posture is preserved: empty SMTP env in dev is allowed.
  - The stdout mail backend refuses to run in prod and produces no log
    line containing the verification link (regression test for the leak).

Modelled on the WORKSPACE_SECRETS_KEY trio in
backend/tests/test_workspace_secrets.py:114-157 — same fail-closed-in-prod
pattern, validated the same way.
"""
from __future__ import annotations

import logging

import pytest


def test_prod_with_empty_smtp_fails_closed(monkeypatch):
    """A prod deploy that forgets any of SMTP_HOST / SMTP_USER /
    SMTP_PASSWORD / MAIL_FROM / APP_BASE_URL must fail at import, not
    silently fall through to the stdout backend (issue #281)."""
    from cryptography.fernet import Fernet
    from pydantic import ValidationError

    from app.core.config import Settings

    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("WORKSPACE_SECRETS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("PASSWORD_PEPPER", "test-pepper")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.05")
    # Leave SMTP_HOST etc. empty.
    for name in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings()
    msg = str(exc_info.value)
    assert "SMTP_HOST" in msg
    # Sanity: error phrasing names variables, not values (prod-hygiene).
    assert "Email verification" in msg


def test_prod_with_dev_default_app_base_url_fails_closed(monkeypatch):
    """APP_BASE_URL=http://localhost:5173 in prod is a misconfiguration:
    verification links would point at a non-public dev URL. The validator
    rejects the dev default explicitly."""
    from cryptography.fernet import Fernet
    from pydantic import ValidationError

    from app.core.config import Settings

    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("WORKSPACE_SECRETS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("PASSWORD_PEPPER", "test-pepper")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.05")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "user")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    monkeypatch.setenv("MAIL_FROM", "noreply@example.com")
    monkeypatch.setenv("APP_BASE_URL", "http://localhost:5173")

    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert "APP_BASE_URL" in str(exc_info.value)


def test_prod_with_full_smtp_boots(monkeypatch):
    """A complete prod config (real SMTP creds + public APP_BASE_URL)
    constructs cleanly."""
    from cryptography.fernet import Fernet

    from app.core.config import Settings

    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("WORKSPACE_SECRETS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("PASSWORD_PEPPER", "test-pepper")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.05")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "user")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    monkeypatch.setenv("MAIL_FROM", "noreply@example.com")
    monkeypatch.setenv("APP_BASE_URL", "https://parts.example.com")

    s = Settings()
    assert s.APP_ENV == "prod"
    assert s.SMTP_HOST == "smtp.example.com"
    assert s.SIGNUP_REQUIRE_EMAIL_VERIFICATION is True


def test_dev_with_empty_smtp_boots(monkeypatch):
    """Dev posture is preserved — no SMTP env required, the stdout
    backend handles delivery."""
    from app.core.config import Settings

    monkeypatch.setenv("APP_ENV", "dev")
    for name in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

    s = Settings()
    assert s.APP_ENV == "dev"
    assert s.SMTP_HOST == ""


def test_send_verification_email_refuses_stdout_in_prod(monkeypatch, caplog, capsys):
    """Defence in depth: if the validator is somehow bypassed and the
    runtime ends up in prod with SMTP_HOST empty, the stdout backend
    must raise rather than log the link.

    Monkeypatch the cached settings instance directly to simulate a
    misconfigured runtime (the validator would normally have prevented
    boot)."""
    from app.core import mail as mail_mod
    from app.core.config import settings

    cfg = settings()
    monkeypatch.setattr(cfg, "APP_ENV", "prod")
    monkeypatch.setattr(cfg, "SMTP_HOST", "")

    link = "https://example.invalid/verify?id=PEND&token=SECRET-TOKEN-XYZ"

    caplog.set_level(logging.DEBUG, logger="app.core.mail")
    capsys.readouterr()  # drain anything pending

    with pytest.raises(RuntimeError):
        mail_mod._send_stdout(to="user@example.com", verification_link=link)

    # Critical assertion: the link/token must not appear anywhere in
    # captured logs or stdout. This is the regression test for the leak.
    captured = capsys.readouterr()
    assert "SECRET-TOKEN-XYZ" not in captured.out
    assert "SECRET-TOKEN-XYZ" not in captured.err
    for record in caplog.records:
        assert "SECRET-TOKEN-XYZ" not in record.getMessage()


def test_send_verification_email_dispatch_skips_stdout_in_prod(monkeypatch):
    """The selector in send_verification_email goes to the SMTP backend
    in prod regardless of whether SMTP_HOST is populated — the
    `and cfg.SMTP_HOST` half of the old conjunct is gone, so an empty
    SMTP_HOST in prod no longer falls through to stdout."""
    from app.core import mail as mail_mod
    from app.core.config import settings

    cfg = settings()
    monkeypatch.setattr(cfg, "APP_ENV", "prod")
    monkeypatch.setattr(cfg, "SMTP_HOST", "")

    called: dict[str, bool] = {"smtp": False, "stdout": False}

    def _fake_smtp(*, to, verification_link):
        called["smtp"] = True

    def _fake_stdout(*, to, verification_link):
        called["stdout"] = True

    monkeypatch.setattr(mail_mod, "_send_smtp", _fake_smtp)
    monkeypatch.setattr(mail_mod, "_send_stdout", _fake_stdout)

    mail_mod.send_verification_email(
        to="user@example.com",
        verification_link="https://example.invalid/verify?id=A&token=B",
    )
    assert called["smtp"] is True
    assert called["stdout"] is False


def test_dev_stdout_backend_logs_warning_only(monkeypatch, caplog, capsys):
    """In dev, the stdout backend logs a single WARNING (not the
    multi-line print banner it used to). The link is still visible — the
    dev workflow depends on it — but nothing is printed to stdout."""
    from app.core import mail as mail_mod
    from app.core.config import settings

    cfg = settings()
    monkeypatch.setattr(cfg, "APP_ENV", "dev")

    link = "http://localhost:5173/verify?id=PEND&token=DEV-TOKEN-VISIBLE"

    capsys.readouterr()
    caplog.clear()
    caplog.set_level(logging.WARNING, logger="app.core.mail")

    mail_mod._send_stdout(to="user@example.com", verification_link=link)

    captured = capsys.readouterr()
    # No `print(...)` banner anymore — stdout/stderr should be empty.
    assert captured.out == ""
    # The WARNING must contain the link (dev workflow needs it).
    assert any(
        "DEV-TOKEN-VISIBLE" in r.getMessage() and r.levelno == logging.WARNING
        for r in caplog.records
    ), "dev stdout backend must log the verification link at WARNING"
