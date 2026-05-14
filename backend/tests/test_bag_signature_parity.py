"""Parity test: Python compute_bag_signature vs TS bagSignature (BE2-015).

The fixture ``web/src/lib/__fixtures__/bagSignatures.json`` is the shared
truth table — the FE test (bagCode.test.ts::bagSignature fixture parity)
verifies the TS side; this file verifies the Python port.

A failure here means the Python normalisation diverged from the TS
implementation and the re-scan correlation flow would break for bags decoded
by ZXing (which emits Control Pictures).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domain.parts.services.bag_signature import compute_bag_signature

_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "web" / "src" / "lib" / "__fixtures__" / "bagSignatures.json"
)


@pytest.fixture(scope="module")
def fixture_data() -> dict:
    return json.loads(_FIXTURE_PATH.read_text())


# ---------------------------------------------------------------------------
# Parity: every raw form of every bag must hash to the expected_signature
# ---------------------------------------------------------------------------

def test_fixture_file_exists():
    assert _FIXTURE_PATH.exists(), f"fixture not found at {_FIXTURE_PATH}"


def test_compute_bag_signature_parity(fixture_data):
    """Python compute_bag_signature must produce the same digest as the TS
    implementation for every (raw, expected_signature) pair in the fixture."""
    bags = fixture_data["bags"]
    assert bags, "fixture has no bags"
    failures = []
    for bag in bags:
        expected = bag["expected_signature"]
        for raw in bag["raws"]:
            got = compute_bag_signature(raw)
            if got != expected:
                failures.append(
                    f"label={bag['label']!r} raw={raw!r}: "
                    f"expected {expected!r} got {got!r}"
                )
    assert not failures, "signature mismatch(es):\n" + "\n".join(failures)


def test_distinct_pairs_differ(fixture_data):
    """Bags that should have distinct signatures do — sanity-check the fixture."""
    pairs = fixture_data.get("distinct_pairs_must_differ", [])
    for a, b in pairs:
        sig_a = compute_bag_signature(a)
        sig_b = compute_bag_signature(b)
        assert sig_a != sig_b, f"expected distinct signatures for {a!r} and {b!r}"


# ---------------------------------------------------------------------------
# Unit tests for edge cases
# ---------------------------------------------------------------------------

def test_returns_none_for_empty_string():
    assert compute_bag_signature("") is None


def test_returns_none_for_whitespace_only():
    assert compute_bag_signature("   ") is None
    assert compute_bag_signature("\t\n") is None


def test_returns_64_char_hex():
    result = compute_bag_signature("STM32F103C8T6")
    assert result is not None
    assert len(result) == 64
    assert result == result.lower()
    assert all(c in "0123456789abcdef" for c in result)


def test_trim_whitespace_gives_same_digest():
    """Leading/trailing whitespace is stripped before hashing."""
    base = compute_bag_signature("STM32F103C8T6")
    assert compute_bag_signature("  STM32F103C8T6  ") == base
    assert compute_bag_signature("\tSTM32F103C8T6\n") == base


def test_zs_whitespace_parity():
    """Unicode Zs whitespace mirrors JavaScript trim() for bag signatures."""
    base = compute_bag_signature("STM32F103C8T6")
    assert compute_bag_signature("\u3000STM32F103C8T6\u3000") == base


def test_control_picture_eot_normalised():
    """␄ (U+2404) should normalise to EOT (0x04) before hashing."""
    raw_with_ctrl = "[)>\x1e06\x1d1PFOO-1\x1d\x04"
    raw_with_picture = "[)>␞06␝1PFOO-1␝␄"
    assert compute_bag_signature(raw_with_ctrl) == compute_bag_signature(raw_with_picture)


def test_control_picture_gs_normalised():
    """␝ (U+241D) should normalise to GS (0x1d) before hashing."""
    raw_with_ctrl = "[)>\x1e06\x1d1PFOO-2\x1d"
    raw_with_picture = "[)>␞06␝1PFOO-2␝"
    assert compute_bag_signature(raw_with_ctrl) == compute_bag_signature(raw_with_picture)


def test_space_picture_normalised():
    """␠ (U+2420) should normalise to ASCII space before hashing."""
    assert compute_bag_signature("A B") == compute_bag_signature("A␠B")
