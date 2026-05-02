"""Shared test factories.

Thin wrappers around the HTTP API — NOT direct DB inserts — so that
workspace-isolation, RBAC, and validation paths still get exercised by
every test. Each factory:

* asserts the call was 2xx,
* returns the most useful field (an id, a response, etc).

We deliberately avoid factory-boy / faker here (see issue #114): plain
functions keep the dependency surface small and the call sites obvious.

If a test needs richer data (extra fields, non-default flags), pass them
as ``**extra`` — every factory forwards unknown kwargs to the request
body so call sites stay one line.

Email-verification (SEC2-014): in dev/test mode
(SIGNUP_REQUIRE_EMAIL_VERIFICATION=False, which is the default when
APP_ENV != "prod") the signup endpoint immediately creates the User +
Workspace and returns 200 — the same behaviour as before this feature
landed. Tests that specifically exercise the email-verification round-trip
should set SIGNUP_REQUIRE_EMAIL_VERIFICATION=true (via override in their
own patching) and call the /auth/signup + /auth/verify endpoints directly.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient


__all__ = [
    "DEFAULT_PASSWORD",
    "signup_user",
    "create_part",
    "create_storage",
    "add_stock",
    "create_project_with_bom",
]


# Single source of truth for the password used across the test suite.
# Must satisfy the strength rule in
# ``app.api.routes.auth._validate_password_strength``.
DEFAULT_PASSWORD = "TestPass-2026-Stronk"


def signup_user(
    client: TestClient,
    email: str | None = None,
    name: str = "Tester",
    password: str = DEFAULT_PASSWORD,
) -> Any:
    """Sign up a fresh user. Returns the raw response so call sites can
    pull whatever they need (``workspace_id``, ``user_id``, etc).

    In tests (APP_ENV=dev, SIGNUP_REQUIRE_EMAIL_VERIFICATION=False) the
    endpoint creates the User + Workspace immediately and returns 200 —
    same shape as before SEC2-014.  The HIBP network call is stubbed via
    the ``hibp_mock`` autouse fixture in conftest.py so no real network
    calls occur.
    """
    email = email or f"u-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post(
        "/api/auth/signup",
        json={"email": email, "name": name, "password": password},
    )
    assert r.status_code == 200, r.text
    return r


def create_part(client: TestClient, name: str = "P", **extra: Any) -> str:
    """Create a local part. Extra kwargs (mpn, manufacturer, …) ride
    along in the request body. Returns the part id."""
    body: dict[str, Any] = {"name": name, "part_type": "local"}
    body.update(extra)
    r = client.post("/api/parts", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def create_storage(client: TestClient, name: str = "Bin", **extra: Any) -> str:
    """Create a storage location. Returns the storage id."""
    body: dict[str, Any] = {"name": name}
    body.update(extra)
    r = client.post("/api/storage", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def add_stock(
    client: TestClient,
    part_id: str,
    qty: int,
    storage_id: str | None = None,
    lot_name: str | None = None,
    **extra: Any,
) -> Any:
    """Add ``qty`` to a part (optionally pinned to a storage / lot).

    Returns the raw response. Call sites that need the entry payload
    can do ``add_stock(...).json()["data"]``.
    """
    body: dict[str, Any] = {"part_id": part_id, "quantity": qty}
    if storage_id is not None:
        body["storage_location_id"] = storage_id
    if lot_name is not None:
        body["lot"] = {"name": lot_name}
    body.update(extra)
    r = client.post("/api/stock/add", json=body)
    assert r.status_code == 200, r.text
    return r


def create_project_with_bom(
    client: TestClient,
    project_name: str,
    bom: list[dict[str, Any]],
) -> str:
    """Create a project and attach BOM entries.

    ``bom`` is a list of dicts shaped like
    ``{part_id, quantity, dnp?, name?}``.
    Returns the project id.
    """
    r = client.post("/api/projects", json={"name": project_name})
    assert r.status_code in (200, 201), r.text
    pid = r.json()["data"]["id"]
    for row in bom:
        r = client.post(
            f"/api/projects/{pid}/entries",
            json={
                "part_id": row.get("part_id"),
                "quantity": row["quantity"],
                "dnp": row.get("dnp", False),
                "name": row.get("name"),
            },
        )
        assert r.status_code in (200, 201), r.text
    return pid
