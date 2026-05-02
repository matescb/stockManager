"""Tests for RequestIdMiddleware (BE2-012 / issue #61).

Assertions:
- Every response carries an `X-Request-Id` header.
- The header value matches the `request_id` key in the JSON body on error
  responses.
- A well-formed inbound `X-Request-Id` header is reused verbatim.
- A malformed inbound value is silently replaced with a fresh id.
"""
from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

_HEX_RE = re.compile(r"^[0-9a-f]{1,64}$")


@pytest.fixture
def client():
    return TestClient(app)


def test_response_carries_x_request_id_header(client):
    r = client.get("/api/health")
    assert "x-request-id" in {k.lower() for k in r.headers}
    rid = r.headers["x-request-id"]
    assert _HEX_RE.match(rid), f"unexpected request_id format: {rid!r}"


def test_error_body_contains_request_id_matching_header(client):
    """A 404 error body must have a top-level `request_id` that equals the
    `X-Request-Id` response header."""
    r = client.get(f"/api/parts/{uuid.uuid4()}")
    # Unauthenticated access → 401 (but we still get a request_id)
    assert r.status_code in (401, 404)
    body = r.json()
    header_rid = r.headers.get("x-request-id")
    assert header_rid, "X-Request-Id header missing"
    assert body.get("request_id") == header_rid, (
        f"body request_id={body.get('request_id')!r} != header {header_rid!r}"
    )


def test_inbound_valid_request_id_is_reused(client):
    """A valid hex `X-Request-Id` sent by the caller must be echoed back."""
    caller_id = uuid.uuid4().hex
    r = client.get("/api/health", headers={"x-request-id": caller_id})
    assert r.headers.get("x-request-id") == caller_id


def test_inbound_invalid_request_id_is_replaced(client):
    """A malformed inbound id (spaces, slashes, too long) must be silently
    replaced with a fresh hex id."""
    bad_ids = [
        "not-hex-at-all",
        "../../../etc/passwd",
        "a" * 65,  # exceeds 64 chars
        "",
    ]
    for bad in bad_ids:
        r = client.get("/api/health", headers={"x-request-id": bad})
        rid = r.headers.get("x-request-id", "")
        assert _HEX_RE.match(rid), f"bad id {bad!r} produced non-hex response: {rid!r}"
        assert rid != bad, f"bad id {bad!r} was reused without replacement"


def test_validation_error_body_has_request_id(client):
    """A 422 Pydantic validation error must surface request_id in the body."""
    # POST /api/auth/login with missing fields → 422
    r = client.post("/api/auth/login", json={})
    assert r.status_code == 422
    body = r.json()
    header_rid = r.headers.get("x-request-id")
    assert header_rid, "X-Request-Id header missing on 422"
    assert body.get("request_id") == header_rid, body


def test_csrf_rejection_carries_request_id(client):
    """A POST to a CSRF-protected path with a hostile Origin gets a 403 from
    CsrfOriginMiddleware. The id middleware wraps CSRF, so the rejection must
    still carry an `X-Request-Id` header and a top-level `request_id` body key
    that match. Locks down the LIFO ordering invariant: regressing the
    add_middleware() order would break this assertion."""
    # POST /api/parts is CSRF-protected (state-changing, not exempt). A bogus
    # Origin (not in cors_origin_list) makes CsrfOriginMiddleware short-circuit
    # with 403 before the route handler runs.
    caller_id = uuid.uuid4().hex
    r = client.post(
        "/api/parts",
        json={"name": "x"},
        headers={
            "origin": "https://evil.example.com",
            "x-request-id": caller_id,
        },
    )
    assert r.status_code == 403, r.text
    body = r.json()
    # The CSRF rejection envelope is `{ data, status }`; we add request_id.
    assert body.get("status", {}).get("category") == "forbidden", body
    header_rid = r.headers.get("x-request-id")
    assert header_rid == caller_id, (
        f"valid inbound id was not propagated through CSRF rejection: {header_rid!r}"
    )
    assert body.get("request_id") == caller_id, body
