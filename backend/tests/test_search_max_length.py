"""BE2-018 / issue #63 — search query param max_length=200 enforcement.

A query string longer than 200 characters must be rejected with HTTP 422
(Pydantic validation error) before any DB work is attempted.
"""
from __future__ import annotations

from tests._factories import signup_user


def test_search_query_too_long_returns_422(client):
    signup_user(client)
    r = client.get("/api/search", params={"q": "a" * 201})
    assert r.status_code == 422, r.text


def test_search_query_at_max_length_is_accepted(client):
    signup_user(client)
    r = client.get("/api/search", params={"q": "a" * 200})
    # 200 OK — no results, but the request itself is valid.
    assert r.status_code == 200, r.text


def test_search_query_missing_returns_422(client):
    signup_user(client)
    r = client.get("/api/search")
    assert r.status_code == 422, r.text


def test_search_query_empty_returns_422(client):
    signup_user(client)
    r = client.get("/api/search", params={"q": ""})
    assert r.status_code == 422, r.text
