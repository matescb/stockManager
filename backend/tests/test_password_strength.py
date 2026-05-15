"""Pin the password-strength check on signup (Sec MED-4) AND guard
against future password-setting routes that forget the validator
(TEST-011).

Today the only endpoint that accepts a password field is
`POST /api/auth/signup`. Login (`POST /api/auth/login`) and invitation
accept (`POST /api/invitations/accept`) do NOT take a password
(`AcceptIn` is `token`-only, with `extra="forbid"`).

Two layers here:
  1. Parametrized weak-password matrix run against every
     password-setting route (today: signup only).
  2. Introspection guard: walk every route on `app`, find any whose
     body schema declares a field named `password` / `new_password` /
     `current_password` / `set_password`, and assert the route is in
     the allow-list of routes known to call `validate_password_strength`.
     Adding a new password-setting route without joining the allow-list
     → this test fails loudly.
"""
from __future__ import annotations

import inspect
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _signup(c: TestClient, *, email: str | None = None, password: str) -> int:
    """Call /auth/signup and return the status code.

    HIBP is already patched for the whole suite by the ``_mock_hibp``
    autouse fixture in conftest.py — no per-call patch needed here.
    In dev/test mode (SIGNUP_REQUIRE_EMAIL_VERIFICATION=False) a valid
    signup returns 200 immediately.
    """
    email = email or f"u-{uuid.uuid4().hex[:8]}@x.com"
    r = c.post(
        "/api/auth/signup",
        json={"email": email, "name": "u", "password": password},
    )
    return r.status_code


# ---------------------------------------------------------------------------
# Per-route weak-password matrix.
#
# Each entry: (route, method, payload-builder taking a `password`).
# Today only signup, but the structure is in place — when a change-
# password / password-reset route eventually ships, add it to this list
# and the entire matrix runs against it for free.
# ---------------------------------------------------------------------------


def _signup_payload(password: str) -> dict:
    return {
        "email": f"u-{uuid.uuid4().hex[:8]}@x.com",
        "name": "u",
        "password": password,
    }


PASSWORD_SETTING_ROUTES = [
    ("POST", "/api/auth/signup", _signup_payload),
]


WEAK_PASSWORD_CASES = [
    ("breach_list_lowercase", "password123", (400,)),
    ("breach_list_uppercase", "PASSWORD123", (400,)),
    ("repetitive_one_char", "aaaaaaaa", (400,)),
    ("two_distinct_chars", "abababab", (400,)),
    # Pydantic min_length=8 may fire before the strength check — accept
    # either 400 or 422 since either path blocks signup.
    ("too_short", "abc12", (400, 422)),
]

# In test mode (SIGNUP_REQUIRE_EMAIL_VERIFICATION=False) signup returns 200.
# In prod mode (or with the flag explicitly set) it returns 202.
_SIGNUP_OK_CODES = (200, 202)


@pytest.mark.parametrize(
    ("method", "route", "make_payload"),
    PASSWORD_SETTING_ROUTES,
    ids=[r[1] for r in PASSWORD_SETTING_ROUTES],
)
@pytest.mark.parametrize(
    ("case_id", "password", "ok_codes"),
    WEAK_PASSWORD_CASES,
    ids=[c[0] for c in WEAK_PASSWORD_CASES],
)
def test_weak_password_rejected(method, route, make_payload, case_id, password, ok_codes):
    """HIBP is stubbed globally by conftest._mock_hibp; no per-test patch needed."""
    c = TestClient(app)
    payload = make_payload(password)
    r = c.request(method, route, json=payload)
    assert r.status_code in ok_codes, (
        f"{method} {route} accepted weak password {case_id!r}: "
        f"got {r.status_code} {r.text}"
    )


def test_strong_password_succeeds_on_signup():
    """A strong password returns 200 (dev mode) or 202 (prod mode, SEC2-014)."""
    c = TestClient(app)
    code = _signup(c, password="VeryStrong-2026-Stockmgr!")
    assert code in _SIGNUP_OK_CODES, code


# ---------------------------------------------------------------------------
# Introspection guard — fails when somebody adds a new password-setting
# route without wiring `validate_password_strength`.
# ---------------------------------------------------------------------------


# Routes whose request body declares a password-shaped field AND are
# known to call `validate_password_strength`. Adding a new route to this
# list is a deliberate act — the guard below is the foot-gun-prevention
# trip-wire for the case where somebody forgets.
#
# Format: (method, path).
PASSWORD_VALIDATING_ROUTES = {
    ("POST", "/api/auth/signup"),
    ("POST", "/api/auth/reset-password"),
}


_PASSWORD_FIELD_NAMES = {"password", "new_password", "current_password", "set_password"}

# Routes that legitimately accept a `password` field but DON'T set it —
# i.e. login compares against the existing hash. These are exempt from
# the strength check (you can't reject the user's already-stored password
# at login time even if the rules tightened since signup).
_PASSWORD_ACCEPTING_NON_SETTING_ROUTES = {
    ("POST", "/api/auth/login"),
}


def _route_body_field_names(route) -> set[str]:
    """Return the set of top-level Pydantic field names declared on the
    request body schema of an APIRoute, or the empty set if the route
    has no body model.

    FastAPI's representation has shifted across pydantic-v1 → pydantic-v2
    eras; we try a small set of attribute paths and give up quietly if
    none match (better to under-report than 500 the test on a route
    that uses a non-Pydantic body type)."""
    body_field = getattr(route, "body_field", None)
    if body_field is None:
        return set()
    # Common Pydantic v2 path: body_field.type_ → BaseModel subclass.
    schema = getattr(body_field, "type_", None)
    if schema is None:
        # FastAPI sometimes stores the FieldInfo wrapper instead.
        info = getattr(body_field, "field_info", None)
        schema = getattr(info, "annotation", None) if info is not None else None
    if schema is None:
        return set()
    fields = getattr(schema, "model_fields", None) or getattr(schema, "__fields__", None) or {}
    return set(fields.keys())


def test_every_password_setting_route_validates_strength():
    """Walk every route on `app`. For any whose body schema has a
    password-shaped field, assert the (method, path) tuple is in
    `PASSWORD_VALIDATING_ROUTES` (i.e. is known to call
    `validate_password_strength`) OR is in
    `_PASSWORD_ACCEPTING_NON_SETTING_ROUTES` (login, where comparing
    against the stored hash is the whole point).

    If somebody later adds e.g. `POST /api/auth/change-password` with a
    `password: str` field, this test fails until that route either
    joins the validator allow-list or explicitly opts out as a
    non-setting route. Cheap; runs in milliseconds.
    """
    from fastapi.routing import APIRoute

    offenders: list[tuple[str, str, set[str]]] = []
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        names = _route_body_field_names(r)
        password_fields = names & _PASSWORD_FIELD_NAMES
        if not password_fields:
            continue
        # The route may be registered with multiple HTTP methods; check
        # each.
        for method in (r.methods or set()):
            key = (method, r.path)
            if key in PASSWORD_VALIDATING_ROUTES:
                continue
            if key in _PASSWORD_ACCEPTING_NON_SETTING_ROUTES:
                continue
            offenders.append((method, r.path, password_fields))

    assert not offenders, (
        "found password-setting route(s) not on the allow-list — they "
        "MUST call validate_password_strength() and be added to "
        "PASSWORD_VALIDATING_ROUTES (or, if read-only like login, to "
        f"_PASSWORD_ACCEPTING_NON_SETTING_ROUTES): {offenders!r}"
    )


def test_invitation_accept_does_not_take_password():
    """Pins `AcceptIn` as token-only with `extra='forbid'`. If somebody
    later adds a `password` field to that schema (e.g. to support
    "set password during invite accept"), this test fails — and the
    fix is to extend the strength validator there AND update the
    introspection guard above (which would also fire)."""
    from app.api.routes.invitations import AcceptIn

    fields = AcceptIn.model_fields
    assert "password" not in fields, (
        "AcceptIn now declares a `password` field — the strength "
        "validator MUST be wired into accept_invitation and the route "
        "added to PASSWORD_VALIDATING_ROUTES."
    )
    # Sanity: extra='forbid' is what makes the API reject a stray
    # `password` field today even without code changes.
    assert AcceptIn.model_config.get("extra") == "forbid"


# Sanity helper kept around so the new introspection test isn't the
# only thing exercising the validator's own code path.
def test_validator_imports_cleanly():
    from app.core.auth import validate_password_strength

    assert inspect.isfunction(validate_password_strength)
