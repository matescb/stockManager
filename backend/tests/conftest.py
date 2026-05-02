from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Set env BEFORE importing app modules so config picks up the test DB.
# This exercises the explicit-DATABASE_URL-override branch in
# Settings._assemble_database_url (INFRA2-005): when DATABASE_URL is
# supplied directly it is used as-is and the POSTGRES_* parts are ignored.
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
from app.infra import db as _infra_db  # noqa: E402
from app.infra.db import Base, get_db  # noqa: E402
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
    in CI rather than at deploy time (BE CRIT-5 in 2026-04-30 review).

    Uses "heads" (plural) to handle the rare case where two migration
    branches share a common ancestor and have not yet been merged into a
    single linear head (e.g. 0025 and 0029 both descend from 0023).
    """
    cfg = AlembicConfig(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "heads")


@pytest.fixture(scope="session", autouse=True)
def engine():
    """Session-scope engine: schema is migrated to head exactly once.

    Replaces the previous per-test `_reset_schema()` + Alembic upgrade
    that cost 4-8 minutes of pytest wall time across the full suite
    (TEST-009). Per-test isolation now comes from the savepoint-rolled-
    back `db` fixture below.
    """
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


@pytest.fixture(autouse=True)
def db(request, engine, monkeypatch):
    """Per-test transactional session with savepoint rollback (TEST-009).

    Canonical SQLAlchemy 2.x "Joining a Session into an External
    Transaction" recipe — uses `join_transaction_mode="create_savepoint"`
    instead of an `after_transaction_end` listener. With this mode the
    Session opens a SAVEPOINT inside the connection-bound outer
    transaction as its root, so:

      * `s.commit()` / `s.rollback()` only ever touch session-internal
        savepoints — they never end the outer transaction.
      * Production `with db.begin_nested():` (see
        `app/domain/stock/service.py:513,577`) opens a *deeper*
        SAVEPOINT inside the session's root savepoint and unwinds
        cleanly on context-manager exit. The previous listener-based
        recipe re-entered `session.begin_nested()` while the SA
        context manager was still in `__exit__`, raising
        "Can't operate on closed transaction inside context manager"
        (issue #148).
      * Roll back the outer transaction at teardown — every row
        written during the test, including via raw `SessionLocal()`
        calls and via FastAPI handlers, evaporates.

    Also overrides `app.infra.db.SessionLocal` so in-test code that
    instantiates `SessionLocal()` directly (e.g. tests that backdoor
    state via raw SQL) stays inside the same outer transaction. And
    overrides `app.dependency_overrides[get_db]` so HTTP tests via
    `client` / `authed_client` see the same rolled-back state, closing
    the foot-gun where HTTP tests inherited cross-test state.
    """
    if request.node.get_closest_marker("real_db"):
        _reset_schema(engine)
        _alembic_upgrade_head(settings().DATABASE_URL)
        RealSession = sessionmaker(
            bind=engine,
            autoflush=False,
            expire_on_commit=False,
            future=True,
        )
        real_session = RealSession()
        try:
            yield real_session
        finally:
            real_session.close()
            _reset_schema(engine)
            _alembic_upgrade_head(settings().DATABASE_URL)
        return

    connection = engine.connect()
    transaction = connection.begin()
    TestSession = sessionmaker(
        bind=connection,
        join_transaction_mode="create_savepoint",
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )
    s = TestSession()

    # Route any in-test direct `SessionLocal()` use to our connection so
    # raw-SQL backdoors (test_attachments, test_invitations, etc.) share
    # the rolled-back transaction.
    monkeypatch.setattr(_infra_db, "SessionLocal", TestSession)
    if getattr(request.module, "SessionLocal", None) is not None:
        monkeypatch.setattr(request.module, "SessionLocal", TestSession)

    # Route the FastAPI `get_db` dep to yield the SAME session as the
    # test fixture (not a fresh `TestSession()` per request) so HTTP
    # writes and direct fixture writes share one savepoint stack.
    # `s.commit()` / `s.rollback()` operate on session-internal
    # savepoints under `create_savepoint` mode — they never end the
    # connection-bound outer transaction, which the teardown rolls back.
    def _override_get_db():
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield s
    finally:
        app.dependency_overrides.pop(get_db, None)
        s.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(db):
    """HTTP client tied to the per-test transactional session.

    Depends on `db` so every HTTP test gets a clean DB by construction.
    Previously `client` did not depend on `db`, so cross-test state
    bled when a test forgot to declare `db` (TEST-009 foot-gun).
    """
    return TestClient(app)


# Re-export the canonical signup factory under the legacy `_signup` name
# so any test still importing it from `conftest` keeps working.
from tests._factories import signup_user as _signup  # noqa: E402,F401


@pytest.fixture
def authed_client(db):
    """Authenticated HTTP client tied to the per-test transactional session.

    Depends on `db` for the same isolation reason as `client`.
    """
    c = TestClient(app)
    _signup(c)
    return c


@pytest.fixture(autouse=True)
def _mock_hibp(monkeypatch):
    """Stub the HIBP k-anonymity check for all tests so no outbound HTTP
    calls are made during the test suite run (SEC2-014).

    Tests that specifically exercise the HIBP path (test_hibp.py) override
    this fixture locally using their own httpx_mock / monkeypatch.
    """
    import unittest.mock as _mock

    with _mock.patch("app.core.auth._hibp_check"):
        yield


# ---------------------------------------------------------------------------
# Opt-out for tests that need real cross-connection commits.
#
# `test_stock_concurrency.py` spawns threads that each open their own
# `TestClient(app)` and need to observe each other's writes — that's
# fundamentally incompatible with a single shared rolled-back
# connection. Such tests should request `real_db` (autouse mode flag)
# instead of `db`; this fixture skips the savepoint plumbing, runs a
# real `_reset_schema()` + `_alembic_upgrade_head()` for the test, and
# leaves prod `SessionLocal` / `get_db` untouched. Slow per-test (~1-2s)
# but only used by the handful of concurrency / cross-connection tests.
# ---------------------------------------------------------------------------


@pytest.fixture
def real_db(engine):
    """Hard-reset DB fixture for tests that need real commits across
    connections (e.g. threading / advisory-lock tests). Slow; do not
    use unless the savepoint pattern can't model the test."""
    _reset_schema(engine)
    _alembic_upgrade_head(settings().DATABASE_URL)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()
