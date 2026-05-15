# Scan and Import

Audience: engineer

The scan-import flow lets an operator run a barcode scanner over a stack of component bags and materialise each bag as a Part (and optional initial stock). This page covers the bag-signature contract, the MIL-STD-130N parser, the bulk-import idempotency table, and the per-row failure model.

For the bag-signature normalisation invariant see [ADR-0006](../adr/0006-bag-signature-normalization.md). For the append-only ledger contract see [ledger](ledger.md).

## `bag_signature` — the correlation key

The single stable correlation key between a re-scanned bag and the row it produced last time. **SHA-256 hex digest of the normalised raw bag code.**

Normalisation pipeline (the order is load-bearing — TS and Python implementations must agree exactly):

1. **JS-compatible trim** — remove leading/trailing ECMAScript whitespace. Python's `str.strip()` strips *more* characters than JS `.trim()` (notably U+001C FS, U+001D GS, U+001E RS, U+001F US which are field separators inside bag codes); using Python's `strip` would change the digest for bags ending with a separator (`backend/app/domain/parts/services/bag_signature.py:55-70`).
2. **`normalizeControlPictures`** — replace Unicode Control Pictures (`U+2400` block) back to their ASCII counterparts. ZXing-C++ emits these instead of raw control chars; every other decoder emits raw chars. Six fixed substitutions: `␄→\x04`, `␜→\x1c`, `␝→\x1d`, `␞→\x1e`, `␟→\x1f`, `␠→ ` (`backend/app/domain/parts/services/bag_signature.py:77-92`).
3. **SHA-256 hex digest** of the UTF-8-encoded normalised string.
4. **Empty after normalisation → `None`** (no signature). Returns `null` from the TS side, `None` from Python.

The TS implementation trims **once**, before `normalizeControlPictures`. Trimming again afterwards would diverge from TS for any bag whose normalised tail is an ASCII space produced by the `␠` substitution (e.g. `"FOO␠"`). The Python mirror replicates this exactly (`backend/app/domain/parts/services/bag_signature.py:108-121`).

### Implementations

| Side | File | Function |
|---|---|---|
| Client | `web/src/lib/bagCode.ts` | `bagSignature()` |
| Server (verifier) | `backend/app/domain/parts/services/bag_signature.py:95-121` | `compute_bag_signature(raw)` |

### Parity test

The shared truth table is `web/src/lib/__fixtures__/bagSignatures.json`. The Python side asserts against it in `backend/tests/test_bag_signature_parity.py`. Both implementations carry a one-line invariant note: if you touch the normalisation pipeline, update the fixture **and** both implementations in the same PR.

`CLAUDE.md` invariant: "If you touch `web/src/lib/bagCode.ts`, keep the normalisation order the same — the signature is the only stable correlation key."

### Storage

`stock_entries.bag_signature: varchar(64)` (`backend/app/domain/stock/models.py:84`). Set only by scan-import; NULL elsewhere. Partial GIN-friendly index `ix_stock_ws_bag_signature` excludes the NULL rows (DB-008 / alembic `0020_partial_bag_signature_index.py`, `backend/app/domain/stock/models.py:40-45`):

```sql
postgresql_where=text("bag_signature IS NOT NULL")
```

The partial predicate is what keeps the index ~1% the size of a full index and stops paying insert-time cost on every (non-scan) ledger write.

### Server-side verification

When the client supplies `bag_signature` AND `raw_bag_code`, the server recomputes the digest independently. Mismatch means a buggy or adversarial client; surfaces as `bag_signature_mismatch` so ops aren't blind to the bug (`backend/app/api/routes/parts_scan.py:226-239`). BE2-015.

## MIL-STD-130N parser

`web/src/lib/bagCode.ts`. Parses 2D Data Matrix codes that Mouser and DigiKey print on component bags. Format spec is MIL-STD-130N / ANSI MH10.8.2: header `[)>` plus separator-delimited records, each prefixed with a Data Identifier code.

Recognised Data Identifiers (`web/src/lib/bagCode.ts:1-31`):

| DI | Field |
|---|---|
| `1P` | Manufacturer part number (the field we actually want) |
| `30P` | Alternate manufacturer P/N (DigiKey) |
| `P` | Distributor part number |
| `Q` | Quantity |
| `1K` | Purchase order number |
| `K` | Customer reference |
| `10D` | Date code (YYWW) |
| `1T` | Lot/batch |
| `1S` | Serial number |
| `13Z` | Arbitrary |
| `1V` | Manufacturer name (sanity check) |

### Why parsing is hard

The format spec uses ASCII control characters as field separators (`RS=0x1e`, `GS=0x1d`, `EOT=0x04`, `FS=0x1c`). Real-world scanners are wildly inconsistent about preserving them: some pass them through verbatim (best case), some replace them with printable substitutes like `#` or `]`, some omit them entirely leaving fields concatenated.

The parser tries three passes, in order (`web/src/lib/bagCode.ts:25-31`):

1. **Split on real or substitute separators.**
2. **Inline DI scanning** — if pass 1 yields a single chunk, locate known DI prefixes by regex and slice between them.
3. **Plain MPN fallback** — if neither pass found a Data Identifier and the input has no `[)>` header, treat the whole string as a plain MPN (the 1D-barcode case).

### Output shape

`BagCode` (`web/src/lib/bagCode.ts:33-55`):

```ts
{
  mpn: string;
  quantity?: number;
  distributorPn?: string;
  manufacturer?: string;
  dateCode?: string;       // YYWW
  lotBatch?: string;       // 1T
  serial?: string;         // 1S — only on serialised parts
  customerRef?: string;    // K (Mouser web order ref)
  poNumber?: string;       // 1K
  lineItem?: string;       // 14K (Mouser line)
  invoiceRef?: string;     // 11K (Mouser invoice)
  raw: string;
}
```

Helpers `bagLotName(b)` and `bagComments(b)` synthesise human-readable strings for the `Lot.name` and `StockEntry.comments` columns at import time (`web/src/lib/bagCode.ts:58-81`).

## `BulkImportIdempotency`

The idempotency cache for the bulk-import-from-scan endpoint. Composite primary key `(workspace_id, key)` — the `workspace_id` PK enforces workspace isolation at the DB level even though application code already filters (`backend/app/domain/parts/models.py:139-152`).

Schema:

| Column | Notes |
|---|---|
| `workspace_id` | FK to `workspaces`, **CASCADE**, part of PK. |
| `key` | `varchar(64)` part of PK. SHA-256 hex of `(workspace_id + ordered row contents)` or a client-supplied UUID4. |
| `result_json` | JSONB. Holds the full API envelope so a cache hit returns verbatim without re-running any logic. |
| `created_at` | `timestamptz`, `server_default=func.now()`. |

Index: `ix_bulk_import_idempotency_ws_created` for the TTL sweep.

**TTL: 24 hours.** Rows older than `_BULK_IMPORT_IDEMPOTENCY_TTL_H` (24) are swept best-effort at the start of each request — bounded table without a background cron (`backend/app/api/routes/parts_scan.py:59,132-142`).

### Content-key order contract

The implicit content key is **order-sensitive**. `_bulk_import_content_key()` serialises each row in request order and joins those serialised blobs before hashing. Two payloads with the same rows in a different order intentionally produce different fallback keys; `backend/tests/test_bulk_import_idempotency.py::test_distinct_orders_distinct_hashes` pins this contract.

Operationally, the stable retry contract is the explicit frontend-supplied `idempotency_key`. If an operator or support engineer must replay a request body without that key, preserve the exact row order. Shuffling rows changes the fallback content key and can bypass the idempotency cache, so first inspect existing parts / stock entries before retrying a shuffled payload. See [scan-import-retry](../runbooks/scan-import-retry.md).

## Bulk-import flow

`POST /api/parts/bulk-import-from-scan` — `backend/app/api/routes/parts_scan.py:79-432`.

The full sequence:

1. **Idempotency key derivation** (`:129-130`): explicit FE-supplied `idempotency_key`, or fall back to a SHA-256 content hash of `(ws_id + request-order row JSON)`. The fallback is order-sensitive; it preserves the submitted sequence rather than sorting rows.
2. **Best-effort TTL sweep** (`:132-142`): delete rows older than 24h. Failure is swallowed.
3. **Cache lookup** — only when an explicit key was supplied. Falling back to content-hash for cache HIT would suppress the duplicate-MPN detection path on a second scan of the same MPN (`:149-156`).
4. **Provider setup** — `make_provider(ws.parts_provider, decrypt(api_key), decrypt(api_secret))`. Returns 400 if not configured.
5. **Per-request deadline** — 60s wall-clock budget (`_BULK_IMPORT_REQUEST_DEADLINE_S`).
6. **Per-row processing** (max 50 rows per request, `ScanImportIn.rows max_length=50`):
   - **Deadline check** — overflow rows return `status='deadline_exceeded'`.
   - **Empty MPN** → `status='invalid'`.
   - **Workspace check on `storage_location_id`** if supplied → `status='invalid'` on cross-workspace.
   - **Server-side bag_signature verification** if `raw_bag_code` supplied → `status='bag_signature_mismatch'` on disagreement.
   - **Bag re-scan recognition**: query `stock_entries` by `(workspace_id, bag_signature)` for the most recent prior row; if found, return `status='bag_rescan'` with `part_id`, `lot_id`, `storage_location_id`, `quantity` so the UI can offer "consume from this bag" instead of double-importing (`:241-264`).
   - **Duplicate MPN check** (workspace-scoped, case-sensitive) → `status='duplicate'` with `part_id`.
   - **Provider lookup** through a function-scope `ThreadPoolExecutor` with per-row 8s timeout (`_BULK_IMPORT_ROW_TIMEOUT_S`). Timed-out futures are abandoned (the worker thread leaks for the duration of the provider socket timeout but the request continues). Failure → `status='lookup_failed'`.
   - **Materialise** the row inside `db.begin_nested()` (savepoint) — Part + provider custom_fields + optional StockEntry. Any raise in this block rolls back **only that row** without losing the rest of the batch (Sec CRIT-6, `:343-365`). Success → `status='created'` with `part_id`, `quantity_added`, `stock_error`.
7. **Executor teardown** with `wait=False, cancel_futures=True` so the request returns even with hung worker threads (`:375-381`).
8. **Idempotency cache write** via `INSERT … ON CONFLICT DO NOTHING` (postgres dialect upsert) — a plain `flush()` on a race would `IntegrityError` and a Session-level rollback would unwind every per-row savepoint write (`:404-432`).
9. **Explicit terminal commit** even though `get_db` commits on clean exit — pins the batch before any post-commit work could raise (BE2-010, `:383-391`).

### Per-row outcome statuses

| Status | When |
|---|---|
| `created` | Part materialised; optional stock entry written. |
| `duplicate` | An active part with this MPN already exists in this workspace. |
| `bag_rescan` | The same bag was scanned before; UI can offer to consume from the existing lot. |
| `bag_signature_mismatch` | Server recompute of the digest disagrees with the client value. |
| `invalid` | Empty MPN or cross-workspace storage location. |
| `lookup_failed` | Provider returned no match, errored, or timed out. |
| `row_failed` | Unexpected exception during materialisation; row rolled back inside its savepoint. |
| `deadline_exceeded` | Request wall-clock budget exhausted before the row was reached. |

## Quick remove from a scanned bag

`POST /api/parts/{part_id}/quick-remove-bag` — TODO(verify): document the exact request shape after reading the schema. The behaviour is "scan a bag, look up the matching prior `bag_rescan`'d entry, write a `remove` row that points at the same `(part, lot, storage)`".

## Service entry points

| Operation | Entry point | Notes |
|---|---|---|
| Compute bag signature | `domain/parts/services/bag_signature.py::compute_bag_signature` | Server-side mirror of `bagSignature()` in TS. Returns `None` on empty input. |
| Provider lookup with cache + breaker | `domain/parts/services/provider_cache.py::lookup_with_cache` | Used by every scan row. |
| Bulk-import endpoint | `api/routes/parts_scan.py::bulk_import_from_scan` | Inline; no separate service module. |

## Things to never do

- **Never re-trim after `normalizeControlPictures`.** The TS side trims once, before. A second trim would diverge for tail-`␠` bags and trigger `bag_signature_mismatch`.
- **Never reorder the control-picture substitution map.** It mirrors the TS list element-for-element (`backend/app/domain/parts/services/bag_signature.py:77-84`).
- **Never use Python's `str.strip()` on a bag code.** It strips field-separator control chars that are valid bag content.
- **Never write `bag_signature` on a non-scan path.** The partial index assumes scan-only population; widening that breaks the cardinality assumption.
- **Never replace the savepoint-per-row pattern with a single transaction.** A single uncaught exception in row N would discard rows 1..N-1's writes — which the operator already saw acknowledged in the per-row outcome list — and the audit trail would diverge from what was actually persisted (Sec CRIT-6).
- **Never shuffle rows when replaying a scan-import payload without the original `idempotency_key`.** Row order is part of the fallback content key.
