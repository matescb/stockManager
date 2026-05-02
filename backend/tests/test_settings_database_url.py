"""Tests for Settings._assemble_database_url (INFRA2-005).

Three cases:
  1. Discrete POSTGRES_* parts only → DSN assembled with URL-encoded password.
  2. Explicit DATABASE_URL + parts → explicit URL wins, parts ignored.
  3. Password containing special characters (@, :, /) → URL-encoded in the DSN.
"""
from __future__ import annotations

import importlib
import sys

import pytest


def _make_settings(env: dict, monkeypatch: pytest.MonkeyPatch):
    """Import a fresh Settings instance with the given env vars set.

    lru_cache on `settings()` must be cleared between calls; we also
    reload the module so each invocation starts from a clean state.
    """
    # Clear any leftover env from the conftest DATABASE_URL setdefault so
    # these tests exercise the parts-assembly path cleanly.
    for key in (
        "DATABASE_URL",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "APP_ENV",
        "SESSION_SECRET",
        "WORKSPACE_SECRETS_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    for key, value in env.items():
        monkeypatch.setenv(key, value)

    # Force a fresh import of config so lru_cache doesn't return a stale
    # instance from a previous test.
    if "app.core.config" in sys.modules:
        importlib.reload(sys.modules["app.core.config"])

    from app.core.config import Settings

    return Settings()


def test_parts_only_assembles_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no DATABASE_URL is given but POSTGRES_* parts are, the DSN
    is assembled from the parts."""
    s = _make_settings(
        {
            "POSTGRES_USER": "appuser",
            "POSTGRES_PASSWORD": "simplepass",
            "POSTGRES_DB": "mydb",
            "POSTGRES_HOST": "pghost",
            "POSTGRES_PORT": "5433",
        },
        monkeypatch,
    )
    assert s.DATABASE_URL == "postgresql+psycopg://appuser:simplepass@pghost:5433/mydb"


def test_explicit_database_url_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """When DATABASE_URL is provided explicitly it takes priority over any
    POSTGRES_* parts — backward-compat for CI / dev / test callers."""
    explicit = "postgresql+psycopg://ci_user:ci_pass@localhost:5432/ci_db"
    s = _make_settings(
        {
            "DATABASE_URL": explicit,
            "POSTGRES_USER": "other_user",
            "POSTGRES_PASSWORD": "other_pass",
            "POSTGRES_DB": "other_db",
        },
        monkeypatch,
    )
    assert s.DATABASE_URL == explicit


def test_special_chars_in_password_are_url_encoded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passwords containing @, :, and / must be percent-encoded so the
    assembled DSN is a valid URL and psycopg parses it correctly."""
    s = _make_settings(
        {
            "POSTGRES_USER": "stockmgr",
            "POSTGRES_PASSWORD": "p@ss:w/ord",
            "POSTGRES_DB": "stockmgr",
            # Use defaults for HOST and PORT
        },
        monkeypatch,
    )
    # @ → %40, : → %3A, / → %2F
    assert "p%40ss%3Aw%2Ford" in s.DATABASE_URL
    # Host and port should still be the defaults
    assert "@db:5432/" in s.DATABASE_URL
