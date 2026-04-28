from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Set env BEFORE importing app modules so config picks up the test DB.
os.environ.setdefault(
    "DATABASE_URL", os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://stockmgr:stockmgr@db:5432/stockmgr_test")
)
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("UPLOAD_DIR", "/tmp/stockmgr-test-uploads")

from app.core.config import settings  # noqa: E402
import app.domain.all_models  # noqa: F401,E402
from app.infra.db import Base  # noqa: E402
from app.main import app  # noqa: E402


def _reset_schema(eng) -> None:
    """Drop and recreate the public schema. Avoids drop_all() failing on
    circular FK dependencies (parts <-> projects)."""
    with eng.begin() as conn:
        conn.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
        conn.exec_driver_sql("CREATE SCHEMA public")


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
    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture
def db(engine):
    _reset_schema(engine)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def client():
    return TestClient(app)


def _signup(client: TestClient, email: str | None = None, name: str = "Tester"):
    email = email or f"u-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/signup", json={"email": email, "name": name, "password": "password123"})
    assert r.status_code == 200, r.text
    return r


@pytest.fixture
def authed_client():
    c = TestClient(app)
    _signup(c)
    return c
