"""Schema and server-side validation tests for bag_signature (BE2-015).

Covers:
- Pattern constraint on bag_signature (must be ^[a-f0-9]{64}$)
- Server-side mismatch detection in POST /api/stock/add
- Server-side mismatch detection in POST /api/parts/bulk-import-from-scan
"""
from __future__ import annotations

import uuid

import pytest

from app.domain.parts.services.bag_signature import compute_bag_signature


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_part(c, name=None):
    name = name or f"P-{uuid.uuid4().hex[:6]}"
    r = c.post("/api/parts", json={"name": name, "part_type": "local"})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


def _create_storage(c, name=None):
    name = name or f"S-{uuid.uuid4().hex[:6]}"
    r = c.post("/api/storage", json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]["id"]


# ---------------------------------------------------------------------------
# Schema-level: bag_signature field is now pattern-constrained
# ---------------------------------------------------------------------------

class TestBagSignatureSchemaConstraint:
    """AddStockIn.bag_signature must match ^[a-f0-9]{64}$."""

    def test_valid_signature_accepted(self, authed_client):
        part_id = _create_part(authed_client)
        storage_id = _create_storage(authed_client)
        raw = "STM32F103C8T6"
        sig = compute_bag_signature(raw)
        assert sig is not None

        r = authed_client.post(
            "/api/stock/add",
            json={
                "part_id": part_id,
                "quantity": 1,
                "storage_location_id": storage_id,
                "bag_signature": sig,
            },
        )
        assert r.status_code in (200, 201), r.text

    def test_signature_with_uppercase_rejected(self, authed_client):
        """Uppercase hex is rejected — schema pattern requires lower-case."""
        part_id = _create_part(authed_client)
        upper_sig = "A" * 64  # upper-case hex chars, correct length
        r = authed_client.post(
            "/api/stock/add",
            json={
                "part_id": part_id,
                "quantity": 1,
                "bag_signature": upper_sig,
            },
        )
        assert r.status_code == 422, r.text

    def test_signature_wrong_length_rejected(self, authed_client):
        """Signatures of length ≠ 64 are rejected at schema level."""
        part_id = _create_part(authed_client)
        for bad_sig in ["abc123", "a" * 63, "a" * 65]:
            r = authed_client.post(
                "/api/stock/add",
                json={
                    "part_id": part_id,
                    "quantity": 1,
                    "bag_signature": bad_sig,
                },
            )
            assert r.status_code == 422, f"expected 422 for sig={bad_sig!r}, got {r.status_code}"

    def test_null_signature_accepted(self, authed_client):
        """bag_signature=null (omitted) is always valid."""
        part_id = _create_part(authed_client)
        r = authed_client.post(
            "/api/stock/add",
            json={
                "part_id": part_id,
                "quantity": 1,
            },
        )
        assert r.status_code in (200, 201), r.text


# ---------------------------------------------------------------------------
# Server-side mismatch: POST /api/stock/add
# ---------------------------------------------------------------------------

class TestAddStockBagSignatureMismatch:

    def test_matching_raw_and_signature_accepted(self, authed_client):
        part_id = _create_part(authed_client)
        raw = "STM32F103C8T6"
        sig = compute_bag_signature(raw)
        r = authed_client.post(
            "/api/stock/add",
            json={
                "part_id": part_id,
                "quantity": 1,
                "bag_signature": sig,
                "raw_bag_code": raw,
            },
        )
        assert r.status_code in (200, 201), r.text

    def test_mismatched_raw_and_signature_rejected(self, authed_client):
        part_id = _create_part(authed_client)
        sig = compute_bag_signature("STM32F103C8T6")
        # Send the wrong raw to trigger a mismatch.
        r = authed_client.post(
            "/api/stock/add",
            json={
                "part_id": part_id,
                "quantity": 1,
                "bag_signature": sig,
                "raw_bag_code": "TOTALLY-DIFFERENT-MPN",
            },
        )
        assert r.status_code == 422, r.text
        body = r.json()
        # Central error handler wraps into {data, status}.
        # The mismatch message lands in status.message.
        assert "status" in body, body
        assert "message" in body["status"], body

    def test_raw_bag_code_without_signature_ignored(self, authed_client):
        """raw_bag_code with no bag_signature is silently ignored — nothing to verify against."""
        part_id = _create_part(authed_client)
        r = authed_client.post(
            "/api/stock/add",
            json={
                "part_id": part_id,
                "quantity": 1,
                "raw_bag_code": "some-raw-code",
            },
        )
        assert r.status_code in (200, 201), r.text

    def test_control_picture_raw_accepted_when_signature_matches(self, authed_client):
        """ZXing pictogram form of raw must hash to the same digest as the
        raw control-char form — parity at the request level."""
        part_id = _create_part(authed_client)
        raw_scandit = "[)>\x1e06\x1d1PFOO-1\x1d"
        raw_zxing   = "[)>␞06␝1PFOO-1␝"

        sig_from_scandit = compute_bag_signature(raw_scandit)
        assert sig_from_scandit is not None

        # Sending the ZXing form should produce the same digest as Scandit.
        r = authed_client.post(
            "/api/stock/add",
            json={
                "part_id": part_id,
                "quantity": 1,
                "bag_signature": sig_from_scandit,
                "raw_bag_code": raw_zxing,
            },
        )
        assert r.status_code in (200, 201), r.text


# ---------------------------------------------------------------------------
# Server-side mismatch: POST /api/parts/bulk-import-from-scan
# The endpoint needs a real provider to proceed past the lookup stage, so
# we test the mismatch short-circuit that fires *before* the provider call.
# ---------------------------------------------------------------------------

class TestBulkImportBagSignatureMismatch:

    def test_mismatch_row_gets_bag_signature_mismatch_status(self, authed_client):
        """A row with a mismatched (raw_bag_code, bag_signature) pair resolves
        with status='bag_signature_mismatch' — no provider call is made."""
        sig = compute_bag_signature("STM32F103C8T6")
        assert sig is not None

        r = authed_client.post(
            "/api/parts/bulk-import-from-scan",
            json={
                "rows": [
                    {
                        "mpn": "STM32F103C8T6",
                        "bag_signature": sig,
                        "raw_bag_code": "WRONG-RAW-CODE",  # mismatch
                    }
                ]
            },
        )
        # The endpoint-level response is 200 (per-row outcomes in body).
        # A missing-provider workspace returns 400 before the row loop,
        # so we accept either 200 (mismatch caught) or 400 (no provider).
        if r.status_code == 400:
            pytest.skip("workspace has no provider configured — can't test row loop")
        assert r.status_code == 200, r.text
        rows = r.json()["data"]["rows"]
        assert rows[0]["status"] == "bag_signature_mismatch"

    def test_no_raw_bag_code_skips_verification(self, authed_client):
        """Without raw_bag_code the signature is accepted verbatim — the row
        proceeds to the provider stage (or fails there for other reasons)."""
        sig = compute_bag_signature("STM32F103C8T6")
        assert sig is not None

        r = authed_client.post(
            "/api/parts/bulk-import-from-scan",
            json={
                "rows": [
                    {
                        "mpn": "STM32F103C8T6",
                        "bag_signature": sig,
                        # no raw_bag_code
                    }
                ]
            },
        )
        # If no provider: 400 — that's fine, the row didn't fail schema validation.
        # If provider: the row will be "lookup_failed" or "created", not "bag_signature_mismatch".
        if r.status_code == 200:
            rows = r.json()["data"]["rows"]
            assert rows[0]["status"] != "bag_signature_mismatch"
        else:
            assert r.status_code == 400


# ---------------------------------------------------------------------------
# find_by_bag_signature endpoint: tightened pattern guard
# ---------------------------------------------------------------------------

class TestFindByBagSignaturePatternGuard:

    def test_lowercase_hex_64_passes_guard(self, authed_client):
        sig = "a" * 64
        r = authed_client.get(f"/api/parts/by-bag-signature/{sig}")
        assert r.status_code == 200, r.text
        # No entry in DB → data: null, but the guard didn't reject it.
        assert r.json()["data"] is None

    def test_uppercase_hex_rejected_by_guard(self, authed_client):
        """Upper-case hex was previously accepted (.isalnum() passes 'A').
        Now the regex rejects it quietly (returns data: null)."""
        sig = "A" * 64
        r = authed_client.get(f"/api/parts/by-bag-signature/{sig}")
        assert r.status_code == 200, r.text
        assert r.json()["data"] is None

    def test_short_sig_rejected_by_guard(self, authed_client):
        for bad in ["abc", "a" * 63, "a" * 65]:
            r = authed_client.get(f"/api/parts/by-bag-signature/{bad}")
            assert r.status_code == 200, r.text
            assert r.json()["data"] is None, bad
