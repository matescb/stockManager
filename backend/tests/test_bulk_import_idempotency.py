"""Tests for bulk-import-from-scan idempotency cache (BE2-003).

Covers:
- First POST → new Parts created, result cached.
- Second POST with same idempotency_key → no new Parts, cached envelope returned.
- POST with different key but same MPNs → duplicates branch fires (existing dedup).
- Workspace-A key must NOT be visible to workspace-B (isolation invariant).
"""
from __future__ import annotations

import uuid

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


def _stub_mouser(mpn: str = "RC0402JR-070R") -> dict:
    return {
        "Errors": [],
        "SearchResults": {
            "NumberOfResult": 1,
            "Parts": [
                {
                    "Manufacturer": "Yageo",
                    "ManufacturerPartNumber": mpn,
                    "Description": "Thick Film Resistors - SMD 0R 1/16W 5% 0402",
                    "Category": "Resistors",
                    "DataSheetUrl": "https://example.com/ds.pdf",
                    "ImagePath": "https://example.com/img.jpg",
                    "ProductDetailUrl": f"https://www.mouser.com/{mpn}",
                    "ProductAttributes": [
                        {"AttributeName": "Resistance", "AttributeValue": "0 Ohms"},
                    ],
                }
            ],
        },
    }


@pytest.fixture
def authed(monkeypatch):
    monkeypatch.setattr(
        "app.domain.parts.providers.mouser._post_mouser",
        lambda url, payload: _stub_mouser(
            mpn=payload["SearchByPartRequest"]["mouserPartNumber"]
        ),
    )
    c = TestClient(app)
    _signup_with_mouser(c)
    return c


def test_idempotency_key_returns_cached_envelope_on_retry(authed):
    """Second POST with same idempotency_key returns cached result without
    creating new Parts."""
    idem_key = uuid.uuid4().hex  # 32-char is fine; max_length=64

    first = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{"mpn": "RC0402JR-070R"}], "idempotency_key": idem_key},
    )
    assert first.status_code == 200, first.text
    first_data = first.json()["data"]
    assert first_data["summary"]["created"] == 1
    first_part_id = first_data["rows"][0]["part_id"]

    # Second call — same idempotency_key.
    second = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{"mpn": "RC0402JR-070R"}], "idempotency_key": idem_key},
    )
    assert second.status_code == 200, second.text
    second_data = second.json()["data"]

    # Envelope must be the cached one — same part_id, same summary.
    assert second_data["summary"]["created"] == 1
    assert second_data["rows"][0]["part_id"] == first_part_id

    # The DB should NOT have a second part with the same MPN.
    parts = authed.get("/api/parts").json()["data"]
    matching = [p for p in parts if p["mpn"] == "RC0402JR-070R"]
    assert len(matching) == 1, f"Expected 1 part but found {len(matching)}"


def test_idempotency_key_cached_result_matches_original_envelope(authed):
    """The cached result envelope is byte-identical to the original response
    (same rows, same summary, same provider name)."""
    key = uuid.uuid4().hex

    first = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{"mpn": "RC0402JR-070R"}], "idempotency_key": key},
    )
    assert first.status_code == 200
    first_data = first.json()["data"]

    # Retry with the same key — must return identical envelope.
    second = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{"mpn": "RC0402JR-070R"}], "idempotency_key": key},
    )
    assert second.status_code == 200
    second_data = second.json()["data"]

    assert second_data == first_data, "Cached envelope differs from original"


def test_different_key_same_mpns_hits_duplicate_branch(authed):
    """A different idempotency_key with the same MPNs goes through the normal
    flow and hits the duplicate-check path (not the idempotency cache)."""
    first = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{"mpn": "RC0402JR-070R"}], "idempotency_key": "key-one"},
    )
    assert first.json()["data"]["summary"]["created"] == 1

    second = authed.post(
        "/api/parts/bulk-import-from-scan",
        json={"rows": [{"mpn": "RC0402JR-070R"}], "idempotency_key": "key-two"},
    )
    body = second.json()["data"]
    # Different key → live execution → hits the duplicate branch.
    assert body["summary"]["duplicate"] == 1
    assert body["rows"][0]["status"] == "duplicate"


def test_idempotency_cache_write_is_upsert_not_plain_insert():
    """Direct unit-level check: the bulk-import route must use
    `INSERT … ON CONFLICT DO NOTHING` for the cache write so a race
    doesn't raise `IntegrityError` on the outer transaction.

    We assert this by inspecting the SQL the route emits: pre-insert a
    row with the target (workspace_id, key); have the route attempt to
    write the same key; verify the route returns 200 and the original
    row content is unchanged (because ON CONFLICT DO NOTHING preserves
    the existing row).
    """
    import app.domain.parts.providers.mouser as mouser_mod
    from datetime import datetime, timezone

    from app.domain.parts.models import BulkImportIdempotency
    from app.infra.db import SessionLocal

    real_post = mouser_mod._post_mouser
    mouser_mod._post_mouser = lambda url, payload: _stub_mouser(
        mpn=payload["SearchByPartRequest"]["mouserPartNumber"]
    )
    try:
        c = TestClient(app)
        ws_id = _signup_with_mouser(c)

        # Compute the deterministic content-hash key the route will use
        # when no explicit idempotency_key is supplied.
        from app.api.routes.parts_scan import _bulk_import_content_key
        from app.domain.parts.schemas import ScanImportRow

        rows = [ScanImportRow(mpn="RC0402JR-070R")]
        content_key = _bulk_import_content_key(ws_id, rows)

        # Pre-insert a "winner" row with the SAME key. This is the state a
        # racing-second writer would observe at flush time.
        sentinel_payload = {
            "rows": [{"sentinel": True}],
            "summary": {"created": 999},
            "provider": "sentinel",
        }
        with SessionLocal() as s:
            s.add(
                BulkImportIdempotency(
                    workspace_id=ws_id,
                    key=content_key,
                    result_json=sentinel_payload,
                    created_at=datetime.now(timezone.utc),
                )
            )
            s.commit()

        # Submit a request that derives the same content-hash key. The
        # route's cache LOOKUP only fires for explicit keys (not the
        # content-hash fallback), so it will go down the WRITE path.
        # With the bug present, the cache write would raise IntegrityError
        # and `db.rollback()` would discard the just-created Part.
        r = c.post(
            "/api/parts/bulk-import-from-scan",
            json={"rows": [{"mpn": "RC0402JR-070R"}]},
        )
        assert r.status_code == 200, r.text
        body = r.json()["data"]
        assert body["summary"]["created"] == 1, body
        new_part_id = body["rows"][0]["part_id"]

        # The Part MUST persist after commit — this is the regression assertion.
        from uuid import UUID as _UUID

        from app.domain.parts.models import Part

        with SessionLocal() as s:
            persisted = s.get(Part, _UUID(new_part_id))
            assert persisted is not None, (
                "Part disappeared after bulk-import — partial-commit "
                "regression: the idempotency-cache write rolled back the "
                "outer transaction."
            )

        # The pre-existing cache row must be unchanged (ON CONFLICT DO NOTHING).
        with SessionLocal() as s:
            row = s.get(BulkImportIdempotency, (_UUID(ws_id), content_key))
            assert row is not None
            assert row.result_json == sentinel_payload, (
                "ON CONFLICT DO NOTHING should preserve the existing row"
            )
    finally:
        mouser_mod._post_mouser = real_post


def test_idempotency_key_is_workspace_scoped():
    """Workspace-A's idempotency key must NOT return workspace-B's cached
    envelope — the cache is keyed on (workspace_id, key) composite."""
    # Two independent workspaces, both with Mouser configured.
    import app.domain.parts.providers.mouser as mouser_mod

    real_post = mouser_mod._post_mouser

    def fake_post(url, payload):
        return _stub_mouser(mpn=payload["SearchByPartRequest"]["mouserPartNumber"])

    mouser_mod._post_mouser = fake_post
    try:
        a = TestClient(app)
        b = TestClient(app)
        _signup_with_mouser(a)
        _signup_with_mouser(b)

        shared_key = "shared-idem-key-12345"

        # Workspace A imports first.
        r_a = a.post(
            "/api/parts/bulk-import-from-scan",
            json={"rows": [{"mpn": "RC0402JR-070R"}], "idempotency_key": shared_key},
        )
        assert r_a.status_code == 200
        assert r_a.json()["data"]["summary"]["created"] == 1
        part_id_a = r_a.json()["data"]["rows"][0]["part_id"]

        # Workspace B uses the SAME key — must NOT see workspace A's result.
        # It should go through live execution (MPN doesn't exist in ws B yet).
        r_b = b.post(
            "/api/parts/bulk-import-from-scan",
            json={"rows": [{"mpn": "RC0402JR-070R"}], "idempotency_key": shared_key},
        )
        assert r_b.status_code == 200
        body_b = r_b.json()["data"]
        # Must be a fresh `created` row, not the cached ws-A result.
        assert body_b["summary"]["created"] == 1
        part_id_b = body_b["rows"][0]["part_id"]
        # Part IDs must differ — these are distinct workspace objects.
        assert part_id_b != part_id_a
    finally:
        mouser_mod._post_mouser = real_post
