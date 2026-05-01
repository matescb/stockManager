# Backend Teardown

Scope: FastAPI routers, domain services, core utilities.
Date: 2026-05-01.
Existing review IDs covered/extended: BE-001..BE-009.

## Backend Issues

### BE2-001: `add_stock` writes are not serialised by the per-part advisory lock

Severity: **Critical**

Evidence:
- `backend/app/domain/stock/service.py:151` `add_stock()` — no call to `_lock_for_stock_write`.
- `backend/app/domain/stock/service.py:252` `remove_stock()` and `:302` `move_stock()` and `:420` `adjust_stock()` all call `_lock_for_stock_write` at entry.
- `backend/app/domain/stock/service.py:184` `add_stock` reads `Workspace.serial_tracking_enabled` and inspects mandatory-default-storage state without holding the lock.

Impact:

The advisory lock that BE-001/BE-003 added to serialise writes on `(workspace_id, part_id)` is bypassed entirely on the addition side. A consumer (`remove`/`build_consume`) and a producer (`add`/`receive`) executing concurrently can observe inconsistent intermediate balances, and two `add_stock` calls writing to the same `single_part_only` storage can both pass the (still-missing — see BE-004) destination check. The 0013 trigger remains the only line of defence on additions, which means the API surfaces an uncaught `IntegrityError` 500 instead of a controlled 4xx whenever the trigger fires.

Fix instruction:

Add `_lock_for_stock_write(db, workspace_id=workspace_id, part_id=payload.part_id)` as the first statement in `add_stock`, before any read or write. Treat `receive()` (`backend/app/domain/orders/service.py:43`) and the `output_lot` write inside `consume()` (`backend/app/domain/builds/service.py:413`) the same way: both insert positive deltas for a part and must take the same lock so the producer–consumer race is closed in both directions.

### BE2-002: `stock_entries` cross-table FKs are columns without DB constraints

Severity: **Critical**

Evidence:
- `backend/app/domain/stock/models.py:55` `order_id = Column(UUID, nullable=True)` — no `ForeignKey(...)`.
- `backend/app/domain/stock/models.py:56` `order_entry_id = Column(UUID, nullable=True)` — no FK.
- `backend/app/domain/stock/models.py:58` `build_id = Column(UUID, nullable=True)` — no FK.
- Only `part_id`, `lot_id`, `storage_location_id`, `project_id`, `related_entry_id`, `created_by`, `workspace_id` carry actual `ForeignKey(...)` declarations.

Impact:

Receive (`orders/service.py:137`) and build consume / produce (`builds/service.py:359, 421`) write `stock_entries.order_id` / `order_entry_id` / `build_id` with no referential integrity. There is also no service-level workspace check on these IDs at write time — an attacker who compromises a service-layer call path can plant cross-workspace correlation IDs, and an order/build hard-delete (none currently exists, but hard-cascade is possible via direct SQL or a migration) would leave dangling pointers that several activity/report queries silently filter on.

Fix instruction:

Add Alembic FKs (`order_id → orders.id ON DELETE SET NULL`, `order_entry_id → order_entries.id ON DELETE SET NULL`, `build_id → builds.id ON DELETE SET NULL`) and the corresponding `ForeignKey(...)` in `models.py`. Backfill validation: a one-shot SELECT for orphaned values in each column before the migration's `ALTER TABLE ... ADD CONSTRAINT`.

### BE2-003: `POST /api/parts/bulk-import-from-scan` issues partial commits and unbounded provider blocking

Severity: **High**

Evidence:
- `backend/app/api/routes/parts.py:820` `bulk_import_from_scan` accepts up to 200 rows (`Field(max_length=200)` at `:817`).
- `backend/app/api/routes/parts.py:928` `lookup = provider.lookup_mpn(mpn)` runs serially per row inside the request.
- `backend/app/api/routes/parts.py:989` `db.commit()` runs once at the end; the per-row savepoint pattern (`db.begin_nested()` at `:963`) commits every surviving row — including those whose provider call already happened — even when the operator's tab navigates away mid-request.

Impact:

A single bulk-import call of 200 rows × ~300–600 ms per provider lookup can hold the request open for one to two minutes, blocking the worker thread (FastAPI sync handler). With `--workers 1` in prod (per `CLAUDE.md`), one client can monopolise the API. There is no idempotency key, so a retry after a timeout will re-create every part again (the dedup check is per-row but the savepoint pattern still re-fires the provider call). The savepoint-style "commit what survived" is also a partial-write surprise from the operator's perspective — a 502/timeout from the proxy returns no response body but rows have already been inserted.

Fix instruction:

Cap the row count (e.g. 50) and impose a wall-clock budget per request. Move the loop off the request thread (BackgroundTasks or a real worker queue) and return a job_id the frontend polls. Add an idempotency key — sha256 of (workspace_id, sorted bag_signatures) — that the second submission detects and returns the prior result for. Document the partial-commit semantics on the route's docstring so a caller reading the code knows what survives.

### BE2-004: `POST /api/workspaces` (create_workspace) lets any authenticated user create unlimited workspaces

Severity: **High**

Evidence:
- `backend/app/api/routes/workspaces.py:43` `create_workspace` is gated only by `CurrentUser` (no `require_role`, no rate limit).
- `backend/app/main.py:141` `workspaces.router` is mounted *without* a router-level `_member_gate`.
- `backend/app/api/routes/auth.py:62` signup is rate-limited at 5/hour, but post-signup workspace creation has no cap.

Impact:

A spammer who signs up once can mint unlimited workspaces (each writes a `workspaces` row + a `workspace_members` row) and use the app as a free key/value store of name strings. There is no quota or rate limit on ownership; the same vector multiplies stock/parts ingestion if combined with BE2-003. Cross-workspace UUIDs minted this way are also useful as an existence-oracle inputs for other endpoints.

Fix instruction:

Add `@limiter.limit("10/hour")` to `create_workspace`. Cap the per-user owned-workspace count (e.g. 5) and return 409 when exceeded. Keep the personal workspace created at signup outside the cap. Add a regression test that the 11th `POST /api/workspaces` returns 409.

### BE2-005: `current_quantity()` is bypassed by ad-hoc aggregations in reports/storage

Severity: **High**

Evidence:
- `CLAUDE.md` invariant: "All quantity reads go through `domain/stock/service.py::current_quantity`".
- `backend/app/api/routes/reports.py:34` `low_stock` aggregates `stock_entries` directly with `func.sum(StockEntry.quantity_delta).group_by(StockEntry.part_id)`.
- `backend/app/api/routes/reports.py:97` `stock_value` does the same per-lot aggregation.
- `backend/app/api/routes/reports.py:155` `expiring_lots` does it again.
- `backend/app/domain/stock/service.py:130` `stock_for_storage` and `:83` `stock_summary_for_part` likewise issue raw `func.sum(...)` selects.

Impact:

The invariant "all quantity reads go through `current_quantity`" is already broken in five places. None of these aggregations apply the `status='on_hand'` filter consistently — they do today, but a future status (e.g. a `quarantine` state) added to the service would silently skip these report paths. The frontend's "low-stock" page and the "stock value" report would then disagree with the canonical `total_for_part`. There is no single chokepoint for changing how stock is summed.

Fix instruction:

Move the multi-part roll-up to the stock service (`bulk_current_quantities(db, *, workspace_id, part_ids, status='on_hand') -> dict[UUID, int]` and a parallel `bulk_reserved`). Make every report and route call it. Mark direct `select(func.sum(StockEntry.quantity_delta))` outside `domain/stock/service.py` as a lint-style violation in CONTRIBUTING.md and add a CI grep.

### BE2-006: BOM import has no payload-size guard before decoding

Severity: **High**

Evidence:
- `backend/app/domain/projects/schemas.py:71` `BomImportPreviewIn.text_b64: str` — no `max_length`.
- `backend/app/domain/projects/schemas.py:99` same on `BomImportCommitIn.text_b64`.
- `backend/app/domain/projects/bom_import.py:27` `_decode_b64(b64)` calls `base64.b64decode(b64)` with no size pre-check.
- `backend/app/domain/projects/bom_import.py:75` materialises `all_rows = [list(r) ...]` for the entire CSV.

Impact:

This was flagged as SEC-007 in the existing review at the security tier; the backend-side picture is worse than that note. There is no streaming and no row cap, so a 50-MB upload through the API request body (allowed by FastAPI default) decodes, decodes UTF-8 with `errors='replace'`, and instantiates 1M-row Python lists in worker memory. A single attacker request can OOM the worker. The autoflush=False session means the half-built `ProjectEntry` rows accumulate in the identity map until the final `flush`/`commit`.

Fix instruction:

Add `text_b64: str = Field(max_length=4_000_000)` (≈3 MB after base64 decode) to both schemas. After decode, assert `len(raw) <= 3_000_000` and raise 413. Cap parsed rows at 5000 and reject overruns with 422. Stream-write the `ProjectEntry` rows in batches of 500 with intermediate `db.flush()` so the session identity map doesn't balloon.

### BE2-007: `move_stock` mutates two `stock_entries` rows then re-mutates the first to set `related_entry_id`

Severity: **High**

Evidence:
- `backend/app/domain/stock/service.py:377` `out_entry = StockEntry(...)` flushed.
- `backend/app/domain/stock/service.py:392` `in_entry = StockEntry(... related_entry_id=out_entry.id)` flushed.
- `backend/app/domain/stock/service.py:408` `out_entry.related_entry_id = in_entry.id; db.flush()`.
- The call site (`backend/app/api/routes/stock.py:64` and `:107`) commits *after* the third flush.

Impact:

There is a window where the first `out_entry` exists with `related_entry_id = NULL` and a second `in_entry` exists with `related_entry_id = out_entry.id` but the back-link hasn't been written. If anything raises between the second flush and the third flush (a trigger, a session-level error, a Python exception), the commit either rolls back both or — worse — `move_stock`'s caller commits while the back-pointer is still NULL. The activity timeline (which uses `related_entry_id` to render the "moved to/from" pair) shows orphaned move_out rows. The lot-split path at `:354` also creates the new `Lot` *before* the `out_entry` write, so a failure between them leaves dangling lots.

Fix instruction:

Insert both `StockEntry`s in one flush by populating `related_entry_id` on `in_entry` after both are constructed but before either is added — assign IDs explicitly at `Lot()`/`StockEntry()` construction time (`id=uuid.uuid4()`). For the lot-split path, use `db.begin_nested()` so a partial failure undoes the new lot too.

### BE2-008: `release_reservations` reads/writes outside the per-part advisory lock

Severity: **High**

Evidence:
- `backend/app/domain/builds/service.py:192` `release_reservations` queries every reserve+release row for the build, then writes `release` rows — without `_lock_for_stock_write`.
- `backend/app/api/routes/builds.py:140` `archive_build` calls `release_reservations` directly.
- `backend/app/domain/builds/service.py:262` `consume()` calls `release_reservations` first thing, also without taking the lock.

Impact:

Concurrent build operations against the same project — one consumer + one archiver, or two consumers of two builds that share BOM parts — race on the `reserved` ledger and can either double-release (writing two release rows for the same reserve, double-counting on `reserved_quantity`) or skip a release entirely if the SELECT and INSERT see different states.

Fix instruction:

Each `release_reservations` and `apply_reservations` call should take `_lock_for_stock_write` for every distinct `part_id` it touches before any read. Easier: aggregate the part_ids first, sort, lock each in deterministic order to prevent deadlocks, then do the read/write loop. Add a concurrency test that exercises archive-while-consuming.

### BE2-009: `404 vs 403` is leaked through `_get_part`/`_get` helpers, but cross-workspace `archive` admits the same oracle

Severity: **High**

Evidence:
- `backend/app/api/routes/parts.py:298` `_get_part` returns 404 for both not-found and cross-workspace — correct.
- `backend/app/api/routes/parts.py:384` `archive_part` is gated by `require_role("admin")` which runs *before* the `_get_part` check.
- `backend/app/core/deps.py:97` `require_role` looks up the caller's role in the *current* workspace (`ws`), not the target object's workspace.

Impact:

A non-admin in workspace A who hits `POST /api/parts/{B-part-id}/archive` gets 403 ("requires role admin+"); an admin in A gets 404. The 403 vs 404 distinction is itself an oracle: the attacker now knows whether they are admin in their *own* workspace, but more importantly, a probe against another endpoint that gates on admin reveals "you're not admin" before even checking that the resource exists. Same pattern exists for `bulk-delete` (`parts.py:408`), `archive_storage`, `archive_order`, `archive_build`, `archive_project`, `restore_*`.

Fix instruction:

Run resource-existence and workspace-binding checks before the role gate, or fold the role check into a custom dependency that takes the resource as an argument. The cheap fix: invert `require_role` so it returns 404 (not 403) when the resource isn't visible — but that requires the dependency to know about the resource, which means converting these archive/restore endpoints to a body-validating pattern (`require_resource_access(Part, role="admin")`).

### BE2-010: `accept_invitation` and other writes lack request-scoped DB transaction boundaries

Severity: **High**

Evidence:
- `backend/app/infra/db.py:19` `get_db()` does not begin or commit a transaction; it just yields the session.
- `backend/app/api/routes/invitations.py:166` `accept_invitation` writes `WorkspaceMember` *and* mutates `WorkspaceInvitation` then calls `db.commit()`. The two writes are not in an explicit transaction.
- Many routes follow the pattern `db.add(...); db.commit()` without a surrounding `with db.begin():`.
- `backend/app/api/routes/auth.py:73` signup writes user + workspace + member + session in three flushes and a single commit — same.

Impact:

SQLAlchemy 2.x's default behaviour with `autoflush=False` is to begin an implicit transaction on first `.execute()`/`.flush()` and to commit it at `db.commit()`. Most routes work, but a route that *raises* between flushes leaves an in-flight transaction that the dependency teardown doesn't roll back — `get_db()` does `try/finally close()`, no explicit `rollback()`. SQLAlchemy 2 will roll back at session close, but only if the underlying connection still belongs to the session — a connection invalidated by an `OperationalError` (e.g. server-side terminate) leaves stale state until the pool recycles. The pre-PR-19/22/25 history shows this is repeatedly an issue.

Fix instruction:

Replace `get_db` with the SA 2.x context-manager idiom:

```python
def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
```

Then drop the per-route `db.commit()` calls (let the dep commit on success). For the few places that need savepoints (the bulk-import-from-scan loop), keep them explicit.

### BE2-011: Provider lookups have no rate limit, no caching, and no upstream timeout cap on the route

Severity: **Medium**

Evidence:
- `backend/app/api/routes/parts_provider.py:22` `lookup_mpn` has no `@limiter.limit(...)`.
- `backend/app/api/routes/parts.py:635` `refresh_from_provider` has no rate limit.
- `backend/app/domain/parts/providers/digikey.py:29` `_TIMEOUT_SEC = 15.0` per request — but the provider issues *two* HTTP calls (token + product details) and a third (keyword fallback) is possible. Worst case: 60s per single lookup.
- No memoisation: a workspace that hits the same MPN twice in five seconds pays for two upstream calls.

Impact:

DigiKey's free tier is 1000 calls/day. A malicious or buggy frontend in a single workspace can burn that budget in minutes. Mouser's per-key rate limits are similar. A 60-second worst-case sync HTTP call inside a sync FastAPI handler with `--workers 1` ties up the only worker; combined with BE2-003, two operators scanning bags simultaneously can stall the whole API. There is no LRU on the result, so the same MPN re-scanned twice in ten seconds doubles the cost.

Fix instruction:

Add `@limiter.limit("30/minute")` per workspace key (`get_remote_address` is wrong here — should be `key_func=lambda: ws.id` if slowapi supports it; otherwise add a manual counter on `Workspace`). Cache `provider.lookup_mpn(mpn)` results for 24h in a small in-process LRU keyed on `(provider_name, mpn)`. Cap total upstream wall-clock at 20 s per call. Add a circuit breaker that returns a 503 after N consecutive failures.

### BE2-012: `validation_exception_handler` does not log validation failures, and `http_exception_handler` does not include a request_id

Severity: **Medium**

Evidence:
- `backend/app/core/responses.py:24` `http_exception_handler` logs only on 5xx (`if exc.status_code >= 500`).
- `backend/app/core/responses.py:71` `validation_exception_handler` doesn't log at all.
- No `X-Request-ID` middleware anywhere; no request-id is included in the JSON envelope.
- `backend/app/main.py:14` configures structured logging but doesn't add a request-id `LogFilter`.

Impact:

When a user reports "I tried to create a part and got an error", the only correlation key is timestamp + endpoint. There's no propagated request id between logs, Sentry, and the JSON returned to the operator. 4xx responses (the 90% case for client errors) silently disappear from the log. Validation failures — which are a strong signal of a frontend regression — leave no trace.

Fix instruction:

Add a small middleware that mints `request.state.request_id = uuid.uuid4().hex` and emits it on every log line and as `X-Request-Id` response header. Spread `request_id` into the `err()` envelope. Log validation failures at INFO with the field list so a frontend regression is visible in the journal.

### BE2-013: `OrderEntryPatch.quantity_ordered` accepts negative values past validation

Severity: **Medium**

Evidence:
- `backend/app/domain/orders/schemas.py:27` `quantity_ordered: int | None = None` — no `ge=0`.
- `backend/app/api/routes/orders.py:224` only checks `data["quantity_ordered"] < e.quantity_received` — true for −5 vs 0.
- Existing review BE-006 already flagged this for the patch path.

Impact:

Extends BE-006: an unreceived entry with `quantity_received = 0` can be patched to `quantity_ordered = -10`. The route check passes (−10 < 0 is true so it raises) — wait, re-read: `data["quantity_ordered"] < e.quantity_received` → `-10 < 0` → True → 400. So this particular fence holds. BUT for a partly-received entry (`quantity_received = 5`), patching to `quantity_ordered = 0` passes the inverse check (`0 < 5` → True → 400, raises). The actual hole is `quantity_ordered = 5` for an entry already receiving 7: `5 < 7` → True → 400. OK so the route always raises. The bug is in *create*: `OrderEntryIn.quantity_ordered: int = Field(ge=0)` allows zero. A zero-ordered entry is a no-op against `outstanding = quantity_ordered - quantity_received`. The order's status math (`_order_status` in `service.py:31`) treats zero-total as "draft" — but it's persisted as "open".

Fix instruction:

Tighten BE-006 to `quantity_ordered: int | None = Field(default=None, ge=1)` on patch, and `Field(ge=1)` on create. Treat 0 as "delete the line" client-side. Add tests for negative + zero rejection.

### BE2-014: `archive_storage` does not check whether the storage holds stock; restore is a no-op

Severity: **Medium**

Evidence:
- `backend/app/api/routes/storage.py:111` `archive_storage` simply sets `archived_at = utcnow()`.
- `backend/app/domain/stock/service.py:166` `add_stock` does check `storage.archived_at is not None` and rejects.
- `backend/app/domain/stock/service.py:309` `move_stock` likewise rejects archived destinations.
- Nothing prevents archiving a storage that currently holds, say, 500 reels of 10 different parts.

Impact:

After archive, the stock keeps showing up under that storage in `stock_for_storage` (no archived filter — `domain/stock/service.py:130`) and in reports, but no one can move or remove it without the operator restoring the storage first. There is no UX cue, no warning, no reservation. Operationally this is the kind of footgun where "I'll just archive that shelf" silently locks 50 part-tuples in place.

Fix instruction:

`archive_storage` should refuse with 409 if any positive on-hand exists (`stock_for_storage(...)`) and instead surface a `force` flag that the frontend can opt into. On `force`, write a synthetic move-out to a system "Unsorted" storage so the inventory remains addressable.

### BE2-015: `bag_signature` is recomputed on the client, never re-validated server-side

Severity: **Medium**

Evidence:
- `backend/app/api/routes/parts.py:443` `find_by_bag_signature` reads `signature: str` and only checks `len == 64 and isalnum`.
- `backend/app/api/routes/parts.py:811` `ScanImportRow.bag_signature: str | None = Field(default=None, max_length=64)`.
- `backend/app/domain/stock/schemas.py:42` `AddStockIn.bag_signature: str | None = Field(default=None, max_length=64)`.
- The canonical normalisation lives only in `web/src/lib/bagCode.ts`. There is no Python-side equivalent.

Impact:

The client computes the signature; the server stores it without verification. A buggy or adversarial client can compute a wrong signature, store it, and the rescan-recognition flow then resolves the wrong bag the next time around — surfacing a stranger's lot to an operator who scanned a different bag. There is also no length=64 hex enforcement on the write side (anywhere up to 64 chars including non-hex passes the schema).

Fix instruction:

Either (a) drop the client-supplied signature and recompute it server-side from the raw bag code (require the raw code in the payload), or (b) add a Python implementation of the same normalisation in `app/domain/parts/services/bag_signature.py` and call it server-side as a verification step. Tighten the schema to `pattern=r'^[a-f0-9]{64}$'`. Add a test that asserts `bag_signature(raw)` (Python) == `bagSignature(raw)` (TypeScript) on a fixture set of real bag codes.

### BE2-016: `_get_part` exposes archived-part visibility inconsistently

Severity: **Medium**

Evidence:
- `backend/app/api/routes/parts.py:298` `_get_part` does NOT filter on `archived_at`.
- `backend/app/api/routes/parts.py:436` `find_by_bag_signature` searches `stock_entries` without joining `parts` → returns `part_id` of an archived part, frontend follows the link, hits `_get_part`, gets the part, can act on it.
- `backend/app/api/routes/parts.py:520` `add_substitute` validates both parts via `_get_part` — but it does not refuse a substitute pointing at an archived part. A future un-archive of B silently re-introduces the substitute relationship for A.
- `backend/app/api/routes/projects.py:142` `add_entry` validates `payload.part_id` via `assert_in_workspace` (which doesn't filter archived either) → BOM entries pointing at archived parts.

Impact:

The "archived" state was meant to soft-delete, but downstream operations don't refuse to bind to archived parts. The MPN unique index excludes archived rows specifically so a replacement can take over (`parts/models.py:33-38`), which means an archived part's MPN can collide with a new one — and the substitutes/BOM relationships from before still resolve to the archived part. The shortage analysis at `backend/app/domain/builds/service.py:103` calls `db.get(Part, e.part_id)` and silently skips when `part is None`, but it does NOT skip archived ones.

Fix instruction:

Add an explicit `include_archived: bool = False` argument to `_get_part` (and its `_get` siblings in `orders.py`, `builds.py`, `projects.py`, `lots.py`, `storage.py`). Default to refusing archived; mutating endpoints (patch, substitute, BOM-add) should never include archived. Read-only endpoints (`get_part`, `part_activity`) can include them. Add a regression test that BOM entries cannot be created against archived parts.

### BE2-017: `current_scanner_license_key` exposes plaintext to viewers (extends SEC-005)

Severity: **Medium**

Evidence:
- `backend/app/api/routes/workspaces.py:84` `current_scanner_license_key` is gated only by the router-level `_member_gate` on `/api/workspaces`... wait — `workspaces` is mounted at `backend/app/main.py:141` *without* `dependencies=_member_gate`.
- That means `current_scanner_license_key` requires only `CurrentWorkspace` — i.e. an active membership of any role, including viewer.
- `decrypt(ws.scanner_license_key)` returns plaintext.

Impact:

Existing review SEC-005 flagged this. After re-reading the wiring, the issue is more severe than that note suggests: the entire `/api/workspaces` router lacks the `_member_gate` that every other resource router has. Viewers can hit any GET on workspaces; the license key is just one example. Future GET endpoints added under this router will silently inherit the same hole.

Fix instruction:

Mount `workspaces.router` with `dependencies=_member_gate` like every other resource router. Add an explicit `Depends(require_role("member"))` on `current_scanner_license_key` regardless. Add a regression test that asserts viewer → 403 on the scanner-license endpoint.

### BE2-018: `search` is unbounded by query length and does five `ILIKE '%q%'` table scans

Severity: **Medium**

Evidence:
- `backend/app/api/routes/search.py:18` `q: str = Query(..., min_length=1)` — no `max_length`.
- `backend/app/api/routes/search.py:20` `like = f"%{q}%"`.
- Five tables (`Part`, `StorageLocation`, `Project`, `Lot`, `Order`) each do an `or_(... ilike ...)` against 3–5 columns.
- No GIN/trigram index on any of those columns; Postgres falls back to a sequential scan per table.

Impact:

A workspace with 100k parts answers each search keystroke with five seqscans against unindexed text columns. With autocomplete typing speed (one keystroke ≈ 100 ms), the server is permanently CPU-bound for that workspace. The query string accepts arbitrary length, so `q = 'a' * 10_000` is also a DoS vector.

Fix instruction:

Add `max_length=200` to `q`. Add a `pg_trgm` GIN index on `(workspace_id, name gin_trgm_ops)` on each table searched (one Alembic migration; postgres ships pg_trgm by default). Add per-IP rate limit (`30/minute`) to the search endpoint. Cap individual table results at 25 (already done) but add an outer total cap of 50 with "more available — refine your search" hint.

### BE2-019: Activity routes hardcode `limit=200` with no paging — and they fan out user_ids without index

Severity: **Medium**

Evidence:
- `backend/app/api/routes/parts.py:613` `part_activity` calls `.limit(200)` for `stock_rows`.
- `backend/app/api/routes/orders.py:271` `order_activity` same.
- `backend/app/api/routes/builds.py:182` `build_activity_route` same.
- `backend/app/api/routes/_activity.py:36` `_user_map` does `db.query(User).filter(User.id.in_(ids)).all()` per call — fine, but the user table has no index on `(id IN (...))` joined to per-row created_by, so each call rebuilds the dict from scratch even if the same user shows up across all 200 rows.

Impact:

Activity is hard-capped at 200 rows with no `before_id` cursor. A part with 5000 stock entries shows only the most recent 200, with no way to paginate. The hard cap also encodes the fact that the activity page is a dead-end for any kind of audit search.

Fix instruction:

Add cursor-based paging: `?before_occurred_at=<isoformat>&limit=200`. Default page size 50, cap at 200. Memoise `_user_map` across the request via `request.state.user_cache` so a page of 200 stock rows by the same operator doesn't re-fetch.

### BE2-020: `WorkspaceMember.unique(workspace_id, user_id)` collides on accept-invitation

Severity: **Medium**

Evidence:
- `backend/app/domain/workspaces/models.py:44` `UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member")`.
- `backend/app/api/routes/invitations.py:187` accept path: `existing = db.query(WorkspaceMember).filter(workspace_id==..., user_id==...).first()`.
- If `existing` is found (any prior membership, e.g. `status='disabled'`), the route mutates it; if not, it inserts.

Impact:

The dedup path is correct for `status='disabled'`, but a race between two parallel POST `/accept` calls (same user accepting two invites for the same workspace) hits a UniqueConstraint violation that surfaces as an uncaught `IntegrityError` 500 rather than a controlled 409. The pending-invite re-use path at `:104` is also vulnerable — two concurrent POSTs see no existing pending invitation, both insert, the second fails with unique violation? — but the model has no unique index on `(workspace_id, email, status='pending')`, so they both succeed and we now have two pending invites with two different `token_hash`es for the same email.

Fix instruction:

Add a partial unique index `uq_workspace_invitation_pending` on `(workspace_id, email) WHERE status='pending'`. Wrap the accept path in a `try/except IntegrityError` that returns 409 with a friendly message. Add a regression test using `pytest -p threading` that fires two concurrent accepts for the same invite token.

### BE2-021: `match_entry` skips workspace check on the entry it patches

Severity: **Medium**

Evidence:
- `backend/app/api/routes/projects.py:233` `match_entry` calls `_get(db, ws.id, project_id)` for the project, but uses `db.get(ProjectEntry, entry_id)` for the entry and only checks `e.workspace_id != ws.id`.
- `backend/app/api/routes/projects.py:240` validates `part` via `db.get(Part, payload.part_id)` + manual `part.workspace_id != ws.id`.
- The pattern doesn't go through `assert_in_workspace`, so the failure-mode is "404 part not found" vs "404 entry not found" — inconsistent with the rest of the file.

Impact:

Less severe than BE-005, but it's a workspace-isolation enforcement mismatch in the same file. Future refactor that drops the manual `e.workspace_id != ws.id` check (because "we already filter by `project_id`") silently re-opens the door, since `e.project_id != p.id` is the only barrier. The pattern should be uniform.

Fix instruction:

Refactor `match_entry`, `del_entry`, `patch_entry` (in both `orders.py` and `projects.py`) to use a shared helper that takes `(parent_id, child_id, ParentModel, ChildModel)` and walks both in one query. This eliminates the manual `child.parent_id != parent.id and child.workspace_id != ws.id` boilerplate that's repeated five times.

### BE2-022: `get_db()` does not roll back on raise

Severity: **Medium**

Evidence:
- `backend/app/infra/db.py:19-24`: `try: yield db; finally: db.close()`. No `except`.
- Several routes catch their own exceptions and call `db.rollback()` (e.g. `stock.py:46`); many do not (`auth.py:65`, `workspaces.py`, all custom_fields/tags/attachments routes).

Impact:

If a route raises a non-`StockError`/`OrderError` exception (validation surfaced by SQL itself, an `OperationalError` from a dropped connection, a programming error like `KeyError`), the session is closed without an explicit rollback. SQLAlchemy 2.x will roll back at session close, but only if the connection is still healthy; on `OperationalError` the connection is invalidated and the in-flight transaction may persist as the *next* request's starting state when the pool returns the same connection. Probable but not certain — depends on `pool_pre_ping=True` (which is set, so likely OK) and slowapi's connection handling.

Fix instruction:

Add an explicit rollback in `get_db`:

```python
def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
```

This complements BE2-010 (which proposes the dep also commits on success).

### BE2-023: SQLAlchemy `_engine` is a module-level global with no pool sizing

Severity: **Low**

Evidence:
- `backend/app/infra/db.py:15` `_engine = create_engine(settings().DATABASE_URL, future=True, pool_pre_ping=True)`.
- No `pool_size`, `max_overflow`, `pool_recycle`, or `pool_timeout` specified.
- SQLAlchemy default `pool_size=5`, `max_overflow=10`. `--workers 1` in prod (per CLAUDE.md) means up to 15 connections to Postgres.

Impact:

Postgres' default `max_connections` is 100; `--workers 1` × 15 = 15 connections — fine. But the implicit defaults are nowhere configured, which means upping uvicorn workers, a future add of background tasks, or a change to the worker model can quietly exhaust connections without any signal. There is no `pool_recycle` (default −1 = never), so a long-lived idle connection across a Postgres `idle_in_transaction_timeout` will start failing with `OperationalError` at first use after the timeout.

Fix instruction:

Set explicit pool config: `pool_size=10, max_overflow=20, pool_recycle=1800, pool_timeout=30`. Document the choice next to the engine declaration. Add a `/api/health` deep check that a) acquires a connection and b) runs `SELECT 1`.

### BE2-024: `bulk_delete_parts` silently skips IDs from other workspaces — no audit

Severity: **Low**

Evidence:
- `backend/app/api/routes/parts.py:408` `bulk_delete_parts` filters by `Part.workspace_id == ws.id`, archives matches, returns `{archived_ids, skipped: len(payload.part_ids) - len(archived_ids)}`.
- The "skipped" bucket combines (a) IDs in another workspace, (b) IDs that don't exist anywhere, (c) IDs that were already archived.
- No audit-log row anywhere; archive timestamps + updated_by are the only trace.

Impact:

Existing review BE-001..009 covers no audit-log gaps. Archive/restore is a destructive operation — particularly bulk-archive, which can take 100 parts off the active list with one click. The only forensic trail is the `archived_at` timestamp on each row. There is no per-action audit log, no who-archived-which-batch, and the API doesn't even tell the operator which IDs failed and why.

Fix instruction:

Add an `audit_log` table (`workspace_id, user_id, action, target_type, target_ids[], created_at, comment`). Write rows for every archive/restore/bulk-delete, every workspace-member change, every permission change, every credential rotation. Surface the table read-only at `/api/audit?since=...` for admins. Differentiate the bulk-delete result into `not_found_ids[]` vs `already_archived_ids[]`.

### BE2-025: Pagination defaults of 200 are quietly larger than the frontend's display limit

Severity: **Low**

Evidence:
- All list endpoints use `limit: int = Query(default=200, le=1000)`.
- Frontend `DataTable` (per CLAUDE.md) does client-side search and sort over the full result set.
- A workspace with 950 parts asks the server for 200, sees pagination is needed, and... there is no cursor or offset parameter. There is no way to fetch the next page.

Impact:

The "default 200, cap 1000" pattern looks like pagination but isn't. A user with 1500 parts gets 1000 max; the other 500 are unreachable from the UI. The `archived=true` flip at least lets them flip filters, but archived parts are 1000-capped too. As a fix list, this is the canary that the system needs real cursor pagination, not just a cap.

Fix instruction:

Implement cursor pagination (`?cursor=<opaque>&limit=50`, `next_cursor` in response) on all list endpoints. The opaque cursor is a base64 of `(last_id, last_sort_key)`. Drop the `le=1000` cap to `le=200`. Update `DataTable` to fetch the next page on scroll/explicit "more" click.

### BE2-026: `lot_history` and `storage_history` return entire history with no limit/cursor

Severity: **Low**

Evidence:
- `backend/app/api/routes/lots.py:147` `lot_history` calls `history_for_lot` with no limit.
- `backend/app/api/routes/storage.py:143` `storage_history` calls `history_for_storage` with no limit.
- `backend/app/domain/stock/service.py:479` `history_for_lot` orders by `occurred_at desc` and has no `LIMIT`.
- `backend/app/domain/stock/service.py:490` `history_for_storage` likewise.

Impact:

A storage with 50k lifetime stock entries returns all of them in one JSON response. A lot with 10k splits/moves does the same. The frontend then renders this in a sortable table. Browser dies first; backend is fine because the data returns quickly — but bandwidth and memory both peak unnecessarily.

Fix instruction:

Add `limit: int = Query(default=200, le=1000)` to both endpoints (mirror the global stock history endpoint). Pass the limit through to `history_for_lot` / `history_for_storage`. Add cursor support per BE2-025 in the same migration.

## Coverage gaps

- I did not cross-reference `backend/app/domain/parts/providers/mouser.py` against `digikey.py` for divergent error handling patterns; only the DigiKey provider was read in detail. Provider catalog drift between server `domain/parts/services/provider.py` and `web/src/lib/providerCatalog.ts` was not directly verified — there is no `domain/parts/services/provider.py` in this repo (the file lives in `domain/parts/providers/base.py`); the path referenced by CLAUDE.md may be stale.
- Frontend `web/src/lib/bagCode.ts` was not re-read; BE2-015 assumes the canonical normalisation is still client-only based on grep evidence.
- I did not run `pytest --collect-only` or `mypy`; the analysis is purely static reading.
- The `attachments`, `custom_fields`, `tags` routers' polymorphic allow-list (`backend/app/api/_helpers.py:42`) currently registers only `"part"`. Frontend usage today only sends `"part"`, so this is consistent; if future code adds e.g. attachments-to-orders, there will be a 400 returned by `assert_polymorphic_in_workspace` with a generic message rather than a permission/validation error — worth noting but not currently a security issue.
- I did not enumerate every `archived_at` callsite for filter consistency; BE2-016 is based on the four most-trafficked endpoints.
- Mouser provider, the Sentry tunnel host validation logic against actual envelope shapes, and the `bom_import.py` mapping-collision case (two `BomMappingField` rows targeting the same `quantity`) were inspected only briefly.
