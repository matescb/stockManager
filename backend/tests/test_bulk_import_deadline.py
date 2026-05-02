"""Tests for per-row provider timeout and request deadline (BE2-003).

Covers:
- Provider call that exceeds _BULK_IMPORT_ROW_TIMEOUT_S → row resolves
  as `lookup_failed` with a timeout message; subsequent rows still run.
- Request deadline: monkey-patch monotonic so the clock appears to expire
  after the first row; remaining rows resolve as `deadline_exceeded`.
"""
from __future__ import annotations

import uuid
from time import monotonic

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup_with_mouser(c: TestClient) -> str:
    r = c.post(
        "/api/auth/signup",
        json={"email": f"u-{uuid.uuid4().hex[:8]}@x.com", "name": "u", "password": "TestPass-2026-Stronk"},
    )
    assert r.status_code == 200, r.text
    c.patch(
        "/api/workspaces/current",
        json={"parts_provider": "mouser", "parts_provider_api_key": "fake-key"},
    )
    return r.json()["data"]["workspace_id"]


def _stub_mouser(mpn: str) -> dict:
    return {
        "Errors": [],
        "SearchResults": {
            "Parts": [
                {
                    "Manufacturer": "Yageo",
                    "ManufacturerPartNumber": mpn,
                    "Description": "Resistor",
                    "DataSheetUrl": None,
                    "ImagePath": None,
                    "ProductDetailUrl": "https://example.com",
                    "ProductAttributes": [],
                }
            ]
        },
    }


def test_per_row_timeout_marks_row_lookup_failed_neighbours_still_run(monkeypatch):
    """A provider call that hangs longer than _BULK_IMPORT_ROW_TIMEOUT_S
    must surface as `lookup_failed` (with a timeout mention) while rows
    before and after it continue to process."""
    import app.api.routes.parts as parts_mod
    import app.domain.parts.providers.mouser as mouser_mod

    # Make row 2 sleep past the per-row timeout by hanging the future submit.
    # We patch the internal ThreadPoolExecutor.submit rather than the network
    # layer so the hang is predictable regardless of OS scheduler behaviour.
    import concurrent.futures as _cf

    real_submit = _cf.ThreadPoolExecutor.submit
    call_count = {"n": 0}

    def slow_submit(self, fn, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            # Return a future that times out immediately by raising on .result().
            fut: _cf.Future = _cf.Future()
            # Cancelling a not-started future and then raising TimeoutError
            # is equivalent to what we want.  Easier: just set exception.
            import threading

            def _raise_timeout():
                import time
                time.sleep(100)  # never finishes

            # Wrap in a real future that is "running" so cancel() won't work,
            # then rely on the timeout parameter of fut.result() in the route.
            # The cleanest approach: monkeypatch fut.result directly.
            real_fut = real_submit(self, fn, *args, **kwargs)

            class _TimeoutFuture:
                def result(self, timeout=None):
                    raise _cf.TimeoutError()

            return _TimeoutFuture()
        return real_submit(self, fn, *args, **kwargs)

    monkeypatch.setattr(_cf.ThreadPoolExecutor, "submit", slow_submit)
    monkeypatch.setattr(
        mouser_mod,
        "_post_mouser",
        lambda url, payload: _stub_mouser(payload["SearchByPartRequest"]["mouserPartNumber"]),
    )
    # Also lower the per-row timeout so the test doesn't need to actually wait.
    monkeypatch.setattr(parts_mod, "_BULK_IMPORT_ROW_TIMEOUT_S", 0.1)

    c = TestClient(app)
    _signup_with_mouser(c)

    r = c.post(
        "/api/parts/bulk-import-from-scan",
        json={
            "rows": [
                {"mpn": "GOOD-MPN-1"},
                {"mpn": "SLOW-MPN"},
                {"mpn": "GOOD-MPN-2"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    rows = r.json()["data"]["rows"]
    statuses = [row["status"] for row in rows]

    assert statuses[0] == "created", statuses
    assert statuses[1] == "lookup_failed", statuses
    assert "timeout" in rows[1]["error"].lower(), rows[1]
    assert statuses[2] == "created", statuses


def test_request_deadline_marks_unprocessed_rows_as_deadline_exceeded(monkeypatch):
    """When the wall-clock deadline expires mid-batch, rows not yet reached
    must be returned with status='deadline_exceeded'."""
    import app.api.routes.parts as parts_mod
    import app.domain.parts.providers.mouser as mouser_mod

    monkeypatch.setattr(
        mouser_mod,
        "_post_mouser",
        lambda url, payload: _stub_mouser(payload["SearchByPartRequest"]["mouserPartNumber"]),
    )

    # Override the request deadline to be essentially zero by making
    # monotonic() appear to already be past the deadline after row 1.
    real_monotonic = monotonic
    call_count = {"n": 0}

    def fake_monotonic():
        call_count["n"] += 1
        # The route calls monotonic() once to set the deadline, then once
        # at the top of each row's iteration.  After the deadline has been
        # set (call 1), the 2nd+ calls return a value past the deadline.
        if call_count["n"] <= 2:
            return real_monotonic()
        # Return a value far in the future so the deadline appears expired.
        return real_monotonic() + 9999.0

    monkeypatch.setattr(parts_mod, "monotonic", fake_monotonic)

    c = TestClient(app)
    _signup_with_mouser(c)

    r = c.post(
        "/api/parts/bulk-import-from-scan",
        json={
            "rows": [
                {"mpn": "FIRST-MPN"},
                {"mpn": "SECOND-MPN"},
                {"mpn": "THIRD-MPN"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    rows = r.json()["data"]["rows"]
    statuses = [row["status"] for row in rows]

    # First row processed before deadline, rest should be deadline_exceeded.
    assert statuses[0] == "created", statuses
    assert all(s == "deadline_exceeded" for s in statuses[1:]), statuses
    summary = r.json()["data"]["summary"]
    assert summary["deadline_exceeded"] == 2
    assert summary["created"] == 1
