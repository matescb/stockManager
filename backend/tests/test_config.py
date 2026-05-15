from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.config import Settings


def _set_required_prod_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("PASSWORD_PEPPER", "pepper")
    monkeypatch.setenv("WORKSPACE_SECRETS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "user")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    monkeypatch.setenv("MAIL_FROM", "noreply@example.com")
    monkeypatch.setenv("APP_BASE_URL", "https://parts.example.com")


def test_password_pepper_required_in_prod(monkeypatch):
    _set_required_prod_env(monkeypatch)
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.05")
    monkeypatch.delenv("PASSWORD_PEPPER", raising=False)

    try:
        Settings(_env_file=None)
    except ValidationError as exc:
        assert "PASSWORD_PEPPER is required" in str(exc)
    else:
        raise AssertionError("prod Settings accepted missing PASSWORD_PEPPER")


def test_sentry_traces_rate_required_in_prod(monkeypatch):
    _set_required_prod_env(monkeypatch)
    monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)

    try:
        Settings(_env_file=None)
    except ValidationError as exc:
        assert "SENTRY_TRACES_SAMPLE_RATE is required" in str(exc)
    else:
        raise AssertionError("prod Settings accepted missing SENTRY_TRACES_SAMPLE_RATE")


def test_sentry_traces_rate_accepts_explicit_low_prod_rate(monkeypatch):
    _set_required_prod_env(monkeypatch)
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.05")

    settings = Settings(_env_file=None)

    assert settings.SENTRY_TRACES_SAMPLE_RATE == 0.05
