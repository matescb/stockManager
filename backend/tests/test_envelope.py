"""API envelope shape contract tests.

Pins the {data, status} response envelope (see CLAUDE.md hard
invariants) for both 2xx and 4xx paths.  v2 teardown TEST-008 flagged
that no test asserts the envelope shape directly today — every test
just reads `r.json()["data"]…` and would silently pass against a bare
payload.

The 4xx tests also pin `core/responses.py::http_exception_handler`
spreading `HTTPException(detail={…})` keys onto the top-level body
(used by the FE for `existing_id` etc. on 409s).

Smoke matrix is intentionally a curated, hard-coded list of one route
per top-level router: introspecting `app.routes` to drive parametrize
would be brittle and isn't required by the issue (TEST-008 fix
instruction explicitly accepts a hand-rolled list).
"""
from __future__ import annotations

import uuid

import pytest

# 2xx smoke matrix — one representative GET per top-level router that
# the authed_client fixture can hit out of the box (i.e. no setup
# beyond signup). Each entry is (label, path).
_AUTHED_2XX_ROUTES: list[tuple[str, str]] = [
    ("auth.me", "/api/auth/me"),
    ("workspaces.list", "/api/workspaces"),
    ("parts.list", "/api/parts"),
    ("storage.list", "/api/storage"),
    ("stock.history", "/api/stock/history"),
    ("projects.list", "/api/projects"),
    ("orders.list", "/api/orders"),
    ("builds.list", "/api/builds"),
    ("reports.low_stock", "/api/reports/low-stock"),
    ("bom_presets.list", "/api/bom-presets"),
    ("tags.list", "/api/tags"),
    ("categories.list", "/api/categories"),
    ("eda.symbols", "/api/eda/symbols"),
    ("search", "/api/search?q=anything"),
    ("health", "/api/health"),
]


_PYDANTIC_422_REQUESTS: list[tuple[str, str, str, dict[str, object], set[str]]] = [
    (
        "auth.signup.empty_body",
        "post",
        "/api/auth/signup",
        {},
        {"body.email", "body.name", "body.password"},
    ),
    (
        "auth.login.empty_body",
        "post",
        "/api/auth/login",
        {},
        {"body.email", "body.password"},
    ),
]


@pytest.mark.parametrize("label,path", _AUTHED_2XX_ROUTES, ids=[r[0] for r in _AUTHED_2XX_ROUTES])
def test_2xx_envelope_shape(authed_client, label: str, path: str):
    """Every successful 2xx body is exactly `{"data", "status"}` with
    `status.category == "ok"` and `status.message` present.  Pins
    `core/responses.py::ok`."""
    r = authed_client.get(path)
    assert r.status_code in (200, 201), f"{label} {path}: {r.status_code} {r.text}"
    body = r.json()
    assert isinstance(body, dict), f"{label}: non-dict body {body!r}"
    assert set(body.keys()) == {"data", "status"}, (
        f"{label}: top-level keys {set(body.keys())} != {{'data','status'}}"
    )
    assert isinstance(body["status"], dict), f"{label}: status is not a dict"
    assert {"category", "message"} <= set(body["status"].keys()), (
        f"{label}: status keys {set(body['status'].keys())} missing category/message"
    )
    assert body["status"]["category"] == "ok", (
        f"{label}: status.category {body['status']['category']!r} != 'ok'"
    )
    assert isinstance(body["status"]["message"], str)


def test_401_envelope(client):
    """Unauthed call to `/api/auth/me` returns the error envelope with
    `data: None` and `status.category == "unauthenticated"`."""
    r = client.get("/api/auth/me")
    assert r.status_code == 401, r.text
    body = r.json()
    assert body["data"] is None
    assert body["status"]["category"] == "unauthenticated"
    assert isinstance(body["status"]["message"], str)


def test_404_envelope(authed_client):
    """Authed lookup of a random UUID part returns the not_found
    envelope."""
    missing = uuid.uuid4()
    r = authed_client.get(f"/api/parts/{missing}")
    assert r.status_code == 404, r.text
    body = r.json()
    assert body["data"] is None
    assert body["status"]["category"] == "not_found"
    assert isinstance(body["status"]["message"], str)


def test_409_envelope_spreads_extras(authed_client):
    """Duplicate-MPN create returns 409 with `existing_id` /
    `existing_name` spread onto the top-level body.  Pins the
    HTTPException(detail=dict) spread behaviour in
    `core/responses.py::http_exception_handler`."""
    first = authed_client.post(
        "/api/parts",
        json={"name": "Resistor", "mpn": "RC0402JR-070R-envelope"},
    )
    assert first.status_code == 201, first.text
    first_id = first.json()["data"]["id"]

    r = authed_client.post(
        "/api/parts",
        json={"name": "Different", "mpn": "RC0402JR-070R-envelope"},
    )
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["data"] is None
    assert body["status"]["category"] == "conflict"
    # Extras spread onto the top level (not nested under data).
    assert body["existing_id"] == first_id
    assert body["existing_name"] == "Resistor"
    # And the spread didn't leak the internal "message" key — it should
    # have been routed into status.message instead.
    assert "message" not in body


@pytest.mark.parametrize(
    "label,method,path,json_body,expected_fields",
    _PYDANTIC_422_REQUESTS,
    ids=[r[0] for r in _PYDANTIC_422_REQUESTS],
)
def test_validation_error_envelope(
    client,
    label: str,
    method: str,
    path: str,
    json_body: dict[str, object],
    expected_fields: set[str],
):
    """Pydantic validation errors return the app error envelope, not
    FastAPI's default `{"detail": [...]}` body."""
    r = getattr(client, method)(path, json=json_body)
    assert r.status_code == 422, r.text
    body = r.json()
    assert set(body.keys()) == {"data", "status", "errors", "request_id"}, (
        f"{label}: unexpected top-level keys {set(body.keys())}"
    )
    assert body["data"] is None
    assert body["status"] == {"category": "validation_error", "message": "validation failed"}
    assert body["request_id"] == r.headers.get("x-request-id")
    assert isinstance(body.get("errors"), list)
    assert body["errors"], f"{label}: errors list should not be empty"
    for entry in body["errors"]:
        assert set(entry.keys()) == {"field", "message"}
        assert isinstance(entry["field"], str)
        assert isinstance(entry["message"], str)
    fields = {entry["field"] for entry in body["errors"]}
    assert expected_fields <= fields, f"{label}: expected {expected_fields}, got {fields}"
