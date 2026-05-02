"""Tests that http_exception_handler logs 4xx at INFO (BE2-012 / #61).

A 404 / 401 response must produce an INFO log record with `status`,
`path`, `method`, and a non-empty `request_id`.

Note on log capture: we attach a temporary handler directly to
`app.core.responses` rather than relying on pytest's `caplog` fixture,
which does not reliably capture records emitted in the TestClient's
thread after `configure_logging()` has replaced the root handlers.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from app.main import app


class _ListHandler(logging.Handler):
    """Simple accumulating handler for test assertions."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextmanager
def _capture_logger(name: str) -> Generator[_ListHandler, None, None]:
    """Temporarily attach a list handler to *name*, yield it, then clean up."""
    handler = _ListHandler()
    logger = logging.getLogger(name)
    orig_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(orig_level)


@pytest.fixture
def client():
    return TestClient(app)


def test_404_produces_info_log(client):
    """GET on a non-existent part UUID triggers a 401 (unauthenticated) or
    404 (not found).  Either way, http_exception_handler should log at INFO."""
    with _capture_logger("app.core.responses") as handler:
        r = client.get(f"/api/parts/{uuid.uuid4()}")

    assert r.status_code in (401, 404)
    records = [rec for rec in handler.records if rec.levelno == logging.INFO]
    assert records, (
        "expected an INFO log record from app.core.responses; got: "
        + str([(r.name, r.levelname, r.getMessage()) for r in handler.records])
    )
    rec = records[0]
    assert getattr(rec, "status", None) in (401, 404), f"unexpected status: {rec.__dict__}"
    assert getattr(rec, "method", None) == "GET", rec.__dict__
    rid = getattr(rec, "request_id", None)
    assert rid, f"request_id missing or empty in log record: {rec.__dict__}"


def test_4xx_log_request_id_matches_header(client):
    """The `request_id` in the log record must equal the `X-Request-Id`
    response header so operators can correlate client reports to journal
    entries."""
    with _capture_logger("app.core.responses") as handler:
        r = client.get(f"/api/parts/{uuid.uuid4()}")

    header_rid = r.headers.get("x-request-id")
    assert header_rid, "X-Request-Id header missing"
    records = [rec for rec in handler.records if rec.levelno == logging.INFO]
    assert records
    log_rid = getattr(records[0], "request_id", None)
    assert log_rid == header_rid, (
        f"log request_id={log_rid!r} != header {header_rid!r}"
    )


def test_4xx_body_contains_request_id(client):
    """The JSON error body must include a top-level `request_id`."""
    r = client.get(f"/api/parts/{uuid.uuid4()}")
    body = r.json()
    header_rid = r.headers.get("x-request-id")
    assert body.get("request_id") == header_rid, (
        f"body request_id={body.get('request_id')!r} != header {header_rid!r}"
    )
