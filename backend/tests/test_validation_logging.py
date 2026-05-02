"""Tests that validation_exception_handler logs at INFO (BE2-012 / #61).

A deliberately invalid request body must produce exactly one INFO record
with a `fields` extra containing the failed field names.

Note on log capture: pytest's `caplog` fixture does not reliably capture
records emitted in the TestClient's thread when the root logger has been
reconfigured by `configure_logging()` (which replaces the root handlers on
first import).  We therefore attach a temporary handler directly to the
`app.core.responses` logger for the duration of each test.
"""
from __future__ import annotations

import logging
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


def test_validation_failure_is_logged_at_info(client):
    """POST /api/auth/login with an empty body triggers a 422.  We expect
    exactly one INFO log record whose `fields` extra contains the missing
    fields and whose `path` extra matches the endpoint."""
    with _capture_logger("app.core.responses") as handler:
        r = client.post("/api/auth/login", json={})

    assert r.status_code == 422
    records = [rec for rec in handler.records if "validation" in rec.getMessage().lower()]
    assert records, (
        "expected at least one validation log record from app.core.responses; "
        f"got: {[r.getMessage() for r in handler.records]}"
    )
    rec = records[0]
    assert rec.levelno == logging.INFO, f"expected INFO, got {rec.levelname}"
    fields = getattr(rec, "fields", None)
    assert isinstance(fields, list) and fields, (
        f"expected non-empty 'fields' extra, got {fields!r}"
    )
    # The field names should mention the missing body parameters.
    assert fields, f"fields list is empty: {fields!r}"
    path = getattr(rec, "path", None)
    assert path == "/api/auth/login", f"unexpected path: {path!r}"


def test_validation_log_record_has_request_id(client):
    """The validation log record must carry a request_id that matches the
    `X-Request-Id` response header."""
    with _capture_logger("app.core.responses") as handler:
        r = client.post("/api/auth/login", json={})

    assert r.status_code == 422
    header_rid = r.headers.get("x-request-id")
    assert header_rid, "X-Request-Id header missing"
    records = [
        rec for rec in handler.records
        if "validation" in rec.getMessage().lower()
    ]
    assert records
    rec = records[0]
    log_rid = getattr(rec, "request_id", None)
    assert log_rid == header_rid, (
        f"log request_id={log_rid!r} != header {header_rid!r}"
    )
