from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.api.routes import stock as stock_routes
from app.main import app
from tests._factories import signup_user

pytestmark = pytest.mark.real_db


class _FakeDiag:
    constraint_name = "stock_nonneg_trigger"


class _FakeOrig(Exception):
    diag = _FakeDiag()


def _stock_nonneg_integrity_error() -> IntegrityError:
    return IntegrityError("INSERT INTO stock_entries ...", {}, _FakeOrig("stock trigger"))


def test_stock_nonneg_integrityerror_returns_409_envelope(monkeypatch):
    client = TestClient(app)
    signup_user(client)

    def raise_from_trigger(*args, **kwargs):
        raise _stock_nonneg_integrity_error()

    monkeypatch.setattr(stock_routes, "remove_stock", raise_from_trigger)

    r = client.post(
        "/api/stock/remove",
        json={
            "part_id": str(uuid4()),
            "quantity": 1,
        },
    )

    assert r.status_code == 409, r.text
    body = r.json()
    assert body["data"] is None
    assert body["status"]["category"] == "conflict"
    assert body["status"]["message"] == "insufficient stock"
    assert body["constraint"] == "stock_nonneg_trigger"
