"""Routing snapshot for /api/parts (#118 / CQ-002).

`parts.py` is being split into focused files (parts_assets, parts_bulk,
parts_provider) without changing any URL. This test enumerates every
APIRoute mounted under `/api/parts` and asserts the (method, path) set
matches a hand-curated snapshot. If any subsequent split-step PR (or
any unrelated change) accidentally drops or moves an endpoint, this
test fails immediately.

Update the snapshot only when the API surface changes deliberately.
"""
from __future__ import annotations

from fastapi.routing import APIRoute

from app.main import app

# (method, path) tuples expected to be mounted under /api/parts.
EXPECTED_PARTS_ROUTES: set[tuple[str, str]] = {
    # Assets
    ("GET", "/api/parts/assets/{ws_id}/{filename}"),
    # CRUD
    ("GET", "/api/parts"),
    ("POST", "/api/parts"),
    ("GET", "/api/parts/{part_id}"),
    ("PATCH", "/api/parts/{part_id}"),
    ("POST", "/api/parts/{part_id}/archive"),
    ("POST", "/api/parts/{part_id}/restore"),
    # Per-part read-only
    ("GET", "/api/parts/{part_id}/stock"),
    ("GET", "/api/parts/{part_id}/lots"),
    ("GET", "/api/parts/{part_id}/activity"),
    ("GET", "/api/parts/{part_id}/sourcing"),
    # Substitutes
    ("POST", "/api/parts/{part_id}/substitutes"),
    ("GET", "/api/parts/{part_id}/substitutes"),
    ("DELETE", "/api/parts/{part_id}/substitutes/{substitute_id}"),
    # Meta members (note path uses {meta_id} for clarity, same router)
    ("GET", "/api/parts/{meta_id}/members"),
    ("POST", "/api/parts/{meta_id}/members"),
    ("DELETE", "/api/parts/{meta_id}/members/{member_id}"),
    # Bulk + scan-import + signature lookup + quick-remove
    ("POST", "/api/parts/bulk-delete"),
    ("POST", "/api/parts/bulk-import-from-scan"),
    ("GET", "/api/parts/by-bag-signature/{signature}"),
    ("POST", "/api/parts/{part_id}/quick-remove-bag"),
    # Provider
    ("POST", "/api/parts/lookup-mpn"),
    ("POST", "/api/parts/{part_id}/refresh-from-provider"),
}


def _enumerate_parts_routes() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        if not r.path.startswith("/api/parts"):
            continue
        for method in r.methods or set():
            # FastAPI auto-adds HEAD for GET routes; ignore.
            if method == "HEAD":
                continue
            out.add((method, r.path))
    return out


def test_no_url_changes():
    """Every (method, path) under /api/parts must match the snapshot.

    Acts as a regression net for the #118 split sequence: extracting
    helpers, then assets, then provider-refresh, then bulk — none of
    those steps should change the URL surface."""
    actual = _enumerate_parts_routes()
    missing = EXPECTED_PARTS_ROUTES - actual
    extra = actual - EXPECTED_PARTS_ROUTES
    assert not missing, f"routes disappeared: {missing}"
    assert not extra, (
        f"new routes mounted under /api/parts but snapshot not updated: {extra}"
    )
