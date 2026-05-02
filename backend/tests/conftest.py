from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Set env BEFORE importing app modules so config picks up the test DB.
os.environ.setdefault(
    "DATABASE_URL", os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://stockmgr:stockmgr@db:5432/stockmgr_test")
)
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("UPLOAD_DIR", "/tmp/stockmgr-test-uploads")
# CORS allow-list must include the TestClient host (`testserver`) so the
# CSRF Origin middleware (SEC2-001) doesn't block every state-changing
# request the suite makes. We force-merge `http://testserver` in even
# if the operator has CORS_ORIGINS pre-set to the dev/prod value, so
# tests never depend on host environment.
_existing_cors = os.environ.get("CORS_ORIGINS", "")
_cors_parts = [p.strip() for p in _existing_cors.split(",") if p.strip()]
if "http://testserver" not in _cors_parts:
    _cors_parts.append("http://testserver")
os.environ["CORS_ORIGINS"] = ",".join(_cors_parts)

# Patch the FastAPI TestClient so it sends `Origin: http://testserver`
# on every request — Starlette's TestClient otherwise sends no Origin
# at all, which the CSRF middleware would (correctly) reject for any
# POST/PATCH/PUT/DELETE. We do this by subclassing rather than passing
# headers per call so existing test files don't need to be touched.
import fastapi.testclient as _tc_mod  # noqa: E402

_orig_test_client_init = _tc_mod.TestClient.__init__


def _patched_init(self, *args, **kwargs):
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault("Origin", "http://testserver")
    kwargs["headers"] = headers
    _orig_test_client_init(self, *args, **kwargs)


_tc_mod.TestClient.__init__ = _patched_init  # type: ignore[method-assign]

from app.core.config import settings  # noqa: E402
import app.domain.all_models  # noqa: F401,E402
from app.infra.db import Base  # noqa: E402
from app.main import app  # noqa: E402


_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _reset_schema(eng) -> None:
    """Drop and recreate the public schema. Avoids drop_all() failing on
    circular FK dependencies (parts <-> projects)."""
    with eng.begin() as conn:
        conn.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
        conn.exec_driver_sql("CREATE SCHEMA public")


def _alembic_upgrade_head(database_url: str) -> None:
    """Run the production migration chain against the test DB. Replaces
    `Base.metadata.create_all(...)` so model-vs-migration drift surfaces
    in CI rather than at deploy time (BE CRIT-5 in 2026-04-30 review)."""
    cfg = AlembicConfig(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def engine():
    eng = create_engine(settings().DATABASE_URL, future=True)
    # ensure DB exists by trying a connection; if it fails, create it via maintenance DB
    try:
        with eng.connect():
            pass
    except Exception:
        admin_url = settings().DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", future=True)
        with admin.connect() as conn:
            db_name = settings().DATABASE_URL.rsplit("/", 1)[-1]
            conn.exec_driver_sql(f'CREATE DATABASE "{db_name}"')
        admin.dispose()
        eng = create_engine(settings().DATABASE_URL, future=True)
    _reset_schema(eng)
    _alembic_upgrade_head(settings().DATABASE_URL)
    return eng


@pytest.fixture
def db(engine):
    _reset_schema(engine)
    _alembic_upgrade_head(settings().DATABASE_URL)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def client():
    return TestClient(app)


# Re-export the canonical signup factory under the legacy `_signup` name
# so any test still importing it from `conftest` keeps working.
from tests._factories import signup_user as _signup  # noqa: E402,F401


@pytest.fixture
def authed_client():
    c = TestClient(app)
    _signup(c)
    return c
