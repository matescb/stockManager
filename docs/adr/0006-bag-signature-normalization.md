# ADR-0006: Bag-signature normalization for scan idempotency

Audience: engineer

- **Status**: Accepted (retro-documented from existing code)
- **Date**: 2026-05-03
- **Supersedes**: —
- **Superseded by**: —

## Context

Mouser and DigiKey print MIL-STD-130N / ANSI MH10.8.2 Data Matrix codes on component bags. Scanning the same physical bag a second time should match the first scan (so the UI can offer "this bag was already received as lot L"). The bag's raw byte string is not stable across scanners — the format uses ASCII control characters as field separators (RS=0x1e, GS=0x1d, EOT=0x04, FS=0x1c) and real-world scanners replace them with `#`, `]`, drop them entirely, or pass them through verbatim. ZXing-C++ replaces them with Unicode Control Picture glyphs (U+2400 block).

The signature has to survive that. If we stored the raw bytes, two scans of the same bag from two scanners would not collide. If we stored the parsed-MPN, two unrelated bags of the same MPN would collide spuriously.

## Decision

Each `stock_entries` row stores `bag_signature`: the SHA-256 hex of the normalised raw bag code. Normalisation runs in a fixed order:

1. ECMAScript-trim — leading/trailing JS-whitespace only (NOT `str.strip()`, which also strips the field-separator control chars 0x1c–0x1f).
2. `normalizeControlPictures` — replace U+2400-block Control Pictures back to their ASCII counterparts.
3. SHA-256 of the UTF-8 bytes.

The TS implementation lives in `web/src/lib/bagCode.ts:177` (`bagSignature`); the Python mirror is `backend/app/domain/parts/services/bag_signature.py`. The migration that adds the column is `0012_stock_entries_bag_signature.py`; a partial index for non-null signatures is in `0020_partial_bag_signature_index.py`.

Re-scanning a bag matches the same signature, which is how the inline "Found bag" UI works. Empty / whitespace-only input returns `None` — no row is correlated against the empty-signature bucket.

## Consequences

- **Good**: One scalar column, one B-tree partial index, `O(1)` re-scan match. Server can independently recompute and reject mismatched client claims (`tests/test_bag_signature_parity.py`).
- **Trade-offs**: The TS and Python normalisations must stay byte-identical. Any divergence produces spurious `bag_signature_mismatch` 422s. Tests pin the parity (`tests/test_bag_signature_parity.py`).
- **What it forbids**:
  - Don't reorder the steps in either implementation. The double-trim case (trim → normalizeControlPictures → trim again) diverges for bags whose normalised tail is `␠` → space.
  - Don't switch the Python mirror to `str.strip()`; it strips ASCII FS/GS/RS/US, which JS `trim()` does not, producing a different digest on bags that end with a separator.
  - Don't drop the `if not normalised: return None` short-circuit — empty signatures would otherwise dedup every restock.
  - Don't change the hash from SHA-256 (column width `String(64)` already pins it).

## Alternatives considered

- **Store the raw bag bytes** — rejected because two scanners produce different byte sequences for the same bag, breaking dedup.
- **Store the parsed MPN as the dedup key** — rejected because two unrelated bags of the same MPN are different physical bags. The signature is a per-bag identity, not a per-part one.

## References

- Source: `web/src/lib/bagCode.ts:177-190` (`bagSignature`)
- Source: `backend/app/domain/parts/services/bag_signature.py`
- Source: `backend/alembic/versions/0012_stock_entries_bag_signature.py`
- Source: `backend/alembic/versions/0020_partial_bag_signature_index.py`
- Tests: `backend/tests/test_bag_signature_parity.py`, `test_bag_signature.py`
- Rule: `CLAUDE.md:114-118`
