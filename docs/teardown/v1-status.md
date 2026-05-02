# v1 (2026-04-30) finding status, reconciled against v2 teardown

This file traverses the v1 review (`review-2026-04-30/`) finding-by-finding and
marks the current state per the v2 teardown (`docs/teardown/`, 2026-05-01) plus
the 25 PRs shipped between 2026-04-30 and 2026-05-01.

`docs/teardown/SUMMARY.md` only credits three commits explicitly (`ff867d4`,
`205ade0`, `6990a18`); the remaining ~22 PRs are reconciled here.

Status legend:

- **RESOLVED** — shipped, v2 does not re-flag.
- **PARTIAL** — shipped, v2 flags a remaining gap (with v2 ID).
- **PARTIAL → closing in #N** — shipped originally, v2 gap is now in flight on the named PR.
- **OPEN** — not shipped, v2 confirms still live (with v2 ID).
- **DEFERRED** — explicitly skipped per user direction (recorded for completeness).

## In-flight PRs (snapshot 2026-05-02)

Three PRs are open against the v2 priority queue. All three have CI green
and are mergeable. The encryption PR carries a manual ops step.

| # | Branch | What it closes | Ops step? |
|---:|---|---|---|
| [#26](https://github.com/matescb/stockManager/pull/26) | `fix/workspace-secrets-key-fail-closed` | `INFRA2-004` + `SEC2-002` (Critical) — finishes Sec HIGH-9 | YES — set `WORKSPACE_SECRETS_KEY` in `/srv/stockmanager/.env.prod` before merge or backend will crash-loop |
| [#27](https://github.com/matescb/stockManager/pull/27) | `fix/sentry-scrubber-default-deny` | `SEC2-005` (High) — finishes Sec HIGH-1 | none |
| [#28](https://github.com/matescb/stockManager/pull/28) | `fix/stock-add-lock-and-null-bucket` | `BE2-001` + `BE2-008` + `DB-002` (Critical/High/High) — finishes BE CRIT-1, closes BE-002 | none |

This file lives on PR #26's branch (commit `304c635`) — it lands on
`main` when #26 is merged.

## Headline

| Tier | Total | RESOLVED | PARTIAL | OPEN | DEFERRED |
|---|---:|---:|---:|---:|---:|
| v1 CRITs | 22 | 14 | 2 | 3 | 3 |
| v1 HIGHs | 43 | 11 | 3 | 28 | 1 |

(v1 master claims 38 HIGHs; the per-area files enumerate 44 — the 6-row gap is
HIGHs that were promoted to CRITs in the unified list and a small counting
discrepancy in the master synth. The table below uses the per-area files as
the source of truth, omitting Arch HIGH-5 because it's already in the CRIT
table as row 7.)

33 of 65 v1 CRITs+HIGHs are still PARTIAL or OPEN in v2's view. v2 also adds
**130 net-new findings** of its own (9 Critical, 36 High); the
recommended-next-5 below targets the v1 PARTIAL set first.

## v1 CRIT status (22 items)

| # | v1 ID | Title | Status | Resolved by | v2 cross-ref |
|--:|---|---|---|---|---|
| 1 | Sec CRIT-1 | Attachment XSS via SVG/HTML; served same-origin | RESOLVED | PR #4 (`965ccf6`) | `SEC2-006` / `SEC2-011` extend the same hardening to the **provider-asset** path which still lacks parity. |
| 2 | BE CRIT-1 | Stock TOCTOU; ledger can go negative | PARTIAL → closing in #28 | PR #11 (`4d27f96`) | `BE2-001` `add_stock` skipped the lock; `BE2-008` `release_reservations` skipped; `DB-002` trigger NULL-bucket diverges from service. |
| 3 | Infra CRIT-2 | Backups never tested, never encrypted, never off-site | RESOLVED | [matescb/vps-backup](https://github.com/matescb/vps-backup) (`15b4a24`) | `INFRA2-003` closed. Backups now run via the project-agnostic vps-backup service: pg_dump + assets tar piped through `age -r`, pushed to a VPSfree NAS dataset over NFS at `/mnt/nas-backups/`, GFS-pruned (14d / 8w / 6m). Restore drill validated end-to-end on 2026-05-02 (smoke test + manual round-trip decrypt). Local fallback retained 7 days. See `docs/deployment.md#backups`. |
| 4 | Infra CRIT-1 | No rollback path; no pre-deploy `pg_dump` | OPEN | — | `INFRA2-001` (Critical). Recommended PR #4 in the next-5 below splits this from the off-host story. |
| 5 | BE CRIT-6 | `bulk_import_from_scan` not transactional; swallows provider errors | PARTIAL | PR #6 (`44ff344`) | `BE2-003` flags no wall-clock budget, no idempotency key, no row-count cap, blocking `--workers 1`. |
| 6 | BE CRIT-3 | BOM consume substitute double-counting across entries | RESOLVED | PR #8 (`5d1e30a`) | (not re-flagged) |
| 7 | Sec CRIT-3 / Arch HIGH-5 | Workspace cookie non-HttpOnly, non-Secure | RESOLVED | PR #3 (`ac69fe7`) | (not re-flagged) |
| 8 | Sec HIGH-2 / Infra CRIT-3 | Backend container runs as root | RESOLVED | PR #3 (`ac69fe7`) | (not re-flagged) |
| 9 | FE CRIT-1 | `<Gate>` remounts AppShell on every nav | RESOLVED | PR #13 (`ab018ca`) | (not re-flagged; `FE2-001` is a different finding — no global 401 handler). |
| 10 | FE CRIT-2 | `ScanditScanner` reloads wasm on every render | RESOLVED | PR #12 (`e59b7a7`) | (not re-flagged) |
| 11 | BE CRIT-4 | `stock_entries.order_id` / `order_entry_id` / `build_id` are bare UUIDs, no FK | OPEN | — | `BE2-002` (Critical) + `DB-001` (High). No PR shipped against this. |
| 12 | BE CRIT-5 | Migrations not run in test fixture | RESOLVED | PR #10 (`b6dccdd`) | `TEST-007` flags downgrade round-trip is still untested (lower-severity follow-up). |
| 13 | BE CRIT-2 | `default_storage_mandatory` bypass | RESOLVED | PR #7 (`73a8ed8`) | (not re-flagged) |
| 14 | Sec CRIT-5 / BE HIGH-10 | `/api/sentry-tunnel` unauth, unrate-limited, unbounded body | RESOLVED | PR #5 (`14290bf`) | (not re-flagged) |
| 15 | Sec CRIT-2 / BE MED-7 | Upload `await file.read()` buffers entire body in RAM | RESOLVED | PR #4 (`965ccf6`) | (not re-flagged) |
| 16 | Infra CRIT-5 | Source maps served publicly | RESOLVED | PR #3 (`ac69fe7`) | (not re-flagged) |
| 17 | Infra CRIT-4 | CI uses mutable-tag actions | RESOLVED | PR #3 (`ac69fe7`) | (not re-flagged) |
| 18 | FE CRIT-3 | Scanner workspace-doc race tears down camera | RESOLVED | PR #12 (`e59b7a7`) | (not re-flagged) |
| 19 | Arch CRIT-1 | README + ARCHITECTURE.md misleading | RESOLVED | PR #9 (`8e96cfe`) | (not re-flagged) |
| 20 | Arch CRIT-2 | `barcodeReader/` graveyard incl. suspected license blob | DEFERRED | — | User direction 2026-05-01: "fake key and it probably won't be used as scanner at all" — Plan #16 dropped. Files remain in tree. |
| 21 | Arch CRIT-3 | `web/public/scandit/*` overrides node_modules wasm | DEFERRED | — | Same user direction as #20. (`web/public/scandit/` not refreshed.) |
| 22 | Sec CRIT-4 | Session cookie `SameSite=Lax` | OPEN | — | `SEC2-001` (Critical) — no CSRF protection at all; SameSite=Lax is bypassable via top-level POST. Severity escalated by v2 in light of `/api/workspaces/{ws}/switch` (`SEC2-004`). |

## v1 HIGH status (38 items)

| # | v1 ID | Title | Status | Resolved by | v2 cross-ref |
|--:|---|---|---|---|---|
| 1 | Sec HIGH-1 | Sentry `send_default_pii=True`; no `before_send` scrubber | PARTIAL → closing in #27 | PR #3 (`ac69fe7`) | `SEC2-005` (High) — scrubber scoped to `/api/workspaces` only; signup, login, invitations, parts-provider, bulk-import all leak. |
| 2 | Sec HIGH-2 | Backend root | RESOLVED | PR #3 (`ac69fe7`) | (covered by Infra CRIT-3 row) |
| 3 | Sec HIGH-3 | Cross-workspace IDOR on attachment / custom-field / tag-link create | RESOLVED | PR #1 (`8ea0a17`) + PR #2 (`f56d84d`) | (not re-flagged for these routers) |
| 4 | Sec HIGH-4 | `/catalog/{token}` no rate-limit, no rotation log | OPEN | — | `SEC2-008` (Medium) timing + workspace-name leak; `SEC2-019` (Low) rotation cadence. |
| 5 | Sec HIGH-5 | Scanner license key reachable by viewer | OPEN | — | `SEC2-012` / `BE2-017` (Medium) — also extends to whole workspaces router lacking `_member_gate`. |
| 6 | Sec HIGH-6 | No security headers (CSP/HSTS/XFO/nosniff) | OPEN | — | `SEC2-009` + `SEC2-010` + `INFRA2-007` (Medium/High). |
| 7 | Sec HIGH-7 | `--forwarded-allow-ips='*'` trusts XFF for rate-limit | OPEN | — | (not explicitly re-flagged in v2; related: `SEC2-017` per-IP bypass). |
| 8 | Sec HIGH-8 | BOM import unbounded base64 + chardet | OPEN | — | `SEC2-007` + `BE2-006` (High). Schema cap not added. |
| 9 | Sec HIGH-9 | Provider creds stored plaintext + leak via Sentry | PARTIAL → closing in #26 | PR #25 (`ff867d4`) | `INFRA2-004` + `SEC2-002` (both Critical) — `WORKSPACE_SECRETS_KEY` not in prod compose, dev fallback used in prod. **Was the highest-leverage gap.** |
| 10 | BE HIGH-1 | N+1 on `GET /api/parts` stock aggregates | OPEN | — | `BE2-005` (High) — `current_quantity` invariant broken in 5 places; bulk roll-up missing. |
| 11 | BE HIGH-2 | No pagination, no user-controllable limits | RESOLVED | PR #17 (`cafb468`) | `BE2-019` flags activity routes still hard-cap at 200 with no cursor. |
| 12 | BE HIGH-3 | Ad-hoc transaction handling per route | OPEN | — | `BE2-010` (High) — `get_db()` does not begin/commit/rollback. |
| 13 | BE HIGH-4 | Provider errors swallowed to generic "upstream unavailable" | OPEN | — | (not re-flagged; partly addressed by PR #18 logging foundation). |
| 14 | BE HIGH-5 | DigiKey OAuth token cache per-instance | OPEN | — | `BE2-011` (Medium) — also no LRU on lookups, no per-workspace rate-limit. |
| 15 | BE HIGH-6 | `consume` uses legacy `db.query(...).all()` style | OPEN | — | (not re-flagged) |
| 16 | BE HIGH-7 | `auth.signup` no transaction guard | OPEN | — | `BE2-010` (High, parent issue covers signup multi-flush). |
| 17 | BE HIGH-8 | BOM commit re-raises raw `Exception` after rollback | OPEN | — | (not re-flagged directly; `BE2-012` covers logging hygiene around 4xx/5xx more broadly). |
| 18 | BE HIGH-9 | `_required` rounds quantity twice | OPEN | — | (not re-flagged) |
| 19 | BE HIGH-10 | Unbounded body on Sentry tunnel (DoS) | RESOLVED | PR #5 (`14290bf`) | (covered by Sec CRIT-5 row) |
| 20 | FE HIGH-1 | Auth bootstrap captures `workspaceId` in stale closure | OPEN | — | (not re-flagged directly; `FE2-003` workspace-switch race is the bigger lever). |
| 21 | FE HIGH-2 | API boundary fully untyped | RESOLVED | PR #16 (`7099a91`) | (zod schemas are opt-in; widening adoption is a follow-up sweep). |
| 22 | FE HIGH-3 | Initial chunk bloated; eager imports of detail tabs | OPEN | — | (not re-flagged in v2; route-lazy work landed earlier per memory `today-2026-05-01.md` 11:00). |
| 23 | FE HIGH-4 | No abort on unmount for in-flight fetches | OPEN | — | (not re-flagged; `FE2-006` `useMutation` migration would absorb this). |
| 24 | FE HIGH-5 | Native `confirm()` / `alert()` for destructive ops | PARTIAL | PR #15 (`54f3c2b`) | `FE2-005` — `PartsList` bulk-delete dialog is a hand-rolled modal that bypasses the new primitive. |
| 25 | FE HIGH-6 | Forms have no validation, no submit-disable | OPEN | — | `FE2-006` (High) — no `useMutation`, every form rolls its own busy flag. |
| 26 | FE HIGH-7 | `qc.invalidateQueries()` with no key | OPEN | — | `FE2-004` (High) — query keys missing workspace dimension; cache not flushed on switch. |
| 27 | FE HIGH-8 | Query keys without workspace scoping | OPEN | — | `FE2-004` (High) — same as above; explicitly re-flagged. |
| 28 | FE HIGH-9 | `BuildDetail` consumption renders array of `<tr>` with index keys | OPEN | — | (not re-flagged) |
| 29 | Infra HIGH-1 | No backend healthcheck; nginx blind | OPEN | — | `INFRA2-002` (Critical) — `/api/health` is a static `{"status":"ok"}`; recommended PR #4 below fixes both. |
| 30 | Infra HIGH-2 | No resource limits, no log rotation | OPEN | — | `INFRA2-006` + `INFRA2-009` (High each). |
| 31 | Infra HIGH-3 | No TLS config in repo | OPEN | — | `INFRA2-007` (High) — Apache vhost still not committed. |
| 32 | Infra HIGH-4 | No edge rate limiting | OPEN | — | `SEC2-017` (Medium) — slowapi still per-process, no Redis backend. |
| 33 | Infra HIGH-5 | No `.dockerignore` | RESOLVED | PR #3 (`ac69fe7`) | (not re-flagged) |
| 34 | Infra HIGH-6 | Image tags not digest-pinned | OPEN | — | `INFRA2-008` (High) covers Dockerfile bloat + lockfile gap. |
| 35 | Infra HIGH-7 | Deploy is destructive on a live tree, no maintenance mode | OPEN | — | `INFRA2-001` + `INFRA2-002` (both Critical) — recommended PR #4 below. |
| 36 | Infra HIGH-8 | `script_stop: true` only catches first failure | OPEN | — | `INFRA2-002` (Critical) — health gate would catch this. |
| 37 | Infra HIGH-9 | Backup script doesn't lock | DEFERRED | — | (Plan #11 backup overhaul deferred; this is a sub-item.) |
| 38 | Infra HIGH-10 | Swagger docs in prod | RESOLVED | PR #3 (`ac69fe7`) | `SEC2-018` (Low) — `Server: uvicorn` header still leaks the stack. |
| 39 | Arch HIGH-1 | CHANGELOG abandoned for 25 commits | RESOLVED | PR #9 (`8e96cfe`) | (not re-flagged) |
| 40 | Arch HIGH-2 | `docs/development.md` migration table stale | RESOLVED | PR #9 (`8e96cfe`) | (not re-flagged; needs another sweep after the recent migrations though). |
| 41 | Arch HIGH-3 | `ARCHITECTURE.md` "Future work" claims dead code is alive | RESOLVED | PR #9 (`8e96cfe`) | (not re-flagged) |
| 42 | Arch HIGH-4 | RBAC `viewer` role identical to `member` for writes | RESOLVED | PR #21 (`6990a18`) | `TEST-002` flags member-rejection regression net is missing (only viewer rejection asserted). |
| 43 | Arch HIGH-6 | 8 of 12 `domain/<name>/` folders are cargo-cult DDD | OPEN | — | (not re-flagged in v2 — cosmetic / refactor; explicit out-of-scope per the original plan). |

(Arch HIGH-5 — `switch_workspace` cookie attributes — is enumerated in the CRIT
table above as row 7 and is not duplicated here.)

## What I claimed shipped that v2 says is still problematic

These are the **PARTIAL** rows above, expanded with what's missing and how big the gap is:

### 1. PR #25 — encrypt workspace secrets at rest

- **What shipped (`ff867d4`)**: Fernet `encrypt`/`decrypt` in `backend/app/core/secrets.py`; column widening; encrypt-at-write / decrypt-at-read on `parts_provider_api_key`, `parts_provider_api_secret`, `scanner_license_key`; idempotent migration `0016`.
- **What v2 flags (`INFRA2-004` + `SEC2-002`, both Critical)**:
  - `WORKSPACE_SECRETS_KEY` is not in `docker-compose.prod.yml`'s backend `environment:` block.
  - `_DEV_DEFAULT_KEY = b"OXmO1Y_-zTtTJ_NXxL5RQqGsbwI3wQAOJ-V_M5HH4_o="` is committed at `backend/app/core/secrets.py:46`.
  - `core/config.py:39` has no validator for `APP_ENV=="prod"` requiring a non-empty key.
  - Result: prod is encrypting credentials with the dev key that ships in this repo.
- **Effort to close**: ~½ day. Add the env wiring + Pydantic validator + rotate every workspace's stored credentials.

### 2. PR #11 — stock TOCTOU advisory lock + non-negative trigger

- **What shipped (`4d27f96`)**: `pg_advisory_xact_lock(hashtextextended(...))` keyed on `(workspace_id, part_id)` in `remove_stock`, `move_stock`, `adjust_stock`; AFTER INSERT trigger `check_stock_nonneg`.
- **What v2 flags**:
  - `BE2-001` (Critical) — `add_stock` (and `receive`, `consume.output_lot`) never take the lock. Producer/consumer race is still open.
  - `BE2-008` (High) — `release_reservations` and `apply_reservations` read/write the reservation ledger outside the lock.
  - `DB-002` (High) — service validation aggregates across all NULL buckets; trigger groups by exact `(part, lot, storage)` tuple. Mismatch surfaces as a 500 from the trigger when callers omit `lot_id` / `storage_location_id` (BE-002 case from v1).
- **Effort to close**: ~1 day. Lock additions, sort-by-id deadlock prevention on multi-part calls, service-vs-trigger NULL alignment + regression test.

### 3. PR #6 — `bulk_import_from_scan` per-row savepoints

- **What shipped (`44ff344`)**: `db.begin_nested()` per row; per-row outcome in the response summary.
- **What v2 flags (`BE2-003`, High)**:
  - 200-row × ~300–600ms-per-provider-call ties up `--workers 1` for 1–2 minutes.
  - No wall-clock budget on the request.
  - No idempotency key — a retry re-creates everything.
  - No background-job offload.
- **Effort to close**: ~1 day if just adding caps + idempotency key; ~2–3 days for a real BackgroundTasks/queue refactor.

### 4. PR #14 — invitation token hashing

- **What shipped (`905bf11`)**: `WorkspaceInvitation.token_hash`, raw token returned only on create, hashed at lookup, rate-limit on `/accept`.
- **What v2 flags (`SEC2-003`, High)**: The invitations fix wasn't mirrored to the auth path. `UserSession.token` (`backend/app/core/auth.py:68-83`) is still stored verbatim; lookup is non-constant-time SQL `=`. A DB dump leaks every active session as a replayable bearer.
- **Effort to close**: ~½ day. Add `token_hash` column to `user_sessions`, drop plaintext column, force re-login.

### 5. PR #4 — attachment hardening

- **What shipped (`965ccf6`)**: MIME allow-list, magic-byte sniff, filename sanitization, `Content-Disposition: attachment`, streaming size cap.
- **What v2 flags**:
  - `SEC2-006` (High) — provider-asset download (`backend/app/domain/parts/services/assets.py`) follows redirects, has no host allow-list, still permits `image/svg+xml`. Stored XSS + SSRF on the same path.
  - `SEC2-011` (Medium) — `GET /api/parts/assets/{ws_id}/{filename}` (`backend/app/api/routes/parts.py:176`) lacks `X-Content-Type-Options: nosniff` and serves inline.
- **Effort to close**: ~1 day. Apply attachment-style hardening to `assets.py` + `parts.py` asset-serve route, drop SVG, add nosniff.

### 6. PR #3 — Sentry `before_send` scrubber

- **What shipped (`ac69fe7`)**: `_scrub_event` removes `request.data` for `PATCH/POST /api/workspaces/*`.
- **What v2 flags (`SEC2-005`, High)**:
  - Signup / login carry plaintext password.
  - Invitation accept carries the raw token.
  - Parts-provider lookup + bulk-import-from-scan have decrypted API keys in scope on 5xx.
  - `with_locals=True` (default with `send_default_pii=True`) ships frame variables.
- **Effort to close**: ~½ day. Default-deny `request.data` for all PATCH/POST, allow-list a small set of read-only endpoints, scrub `frames[*].vars.{password,api_key,api_secret,scanner_license_key,token,payload}`, set `with_locals=False`.

## Recommended next 5 PRs

Drawn from `docs/teardown/SUMMARY.md` Top-20 ∩ {PARTIAL, OPEN}, ranked by leverage:

### 1. fix(infra): wire `WORKSPACE_SECRETS_KEY` through prod compose; fail-closed in prod; rotate creds — ✅ Submitted as [PR #26](https://github.com/matescb/stockManager/pull/26) (CI green, awaiting ops step + merge)

- **v2 IDs**: `INFRA2-004` + `SEC2-002` (both Critical)
- **v1 IDs**: Sec HIGH-9 (final close)
- **Why first**: Highest leverage. Until this lands, the entire encryption-at-rest project is no-op in prod.
- **Files**:
  - `docker-compose.prod.yml` — add `WORKSPACE_SECRETS_KEY: ${WORKSPACE_SECRETS_KEY}` to backend `environment:` block.
  - `backend/app/core/config.py` — Pydantic validator: `raise` if `APP_ENV=="prod"` and key empty or equals `_DEV_DEFAULT_KEY`.
  - `backend/app/core/secrets.py` — replace committed dev default with a per-process random key when unset and `APP_ENV != "prod"`.
  - `deploy/.env.prod.example` — keep variable, document key generation command.
  - `backend/tests/test_workspace_secrets.py` — pin "boots with `APP_ENV=prod` + empty key fails fast" + "boots with valid key succeeds".
- **Operational step**: rotate every workspace's Mouser/DigiKey/Scandit credential after deploy (assume the dev key was compromised).
- **Effort**: ~½ day.

### 2. fix(security): default-deny request bodies in Sentry scrubber + scrub `frame.vars` — ✅ Submitted as [PR #27](https://github.com/matescb/stockManager/pull/27) (CI green, no ops step)

- **v2 IDs**: `SEC2-005` (High)
- **v1 IDs**: Sec HIGH-1 (final close)
- **Files**:
  - `backend/app/main.py::_scrub_event` — invert: default-deny `request.data` for all PATCH/POST, allow-list a small read-only set; walk `frames[*].vars` and `breadcrumbs[*].data` for `password`, `api_key`, `api_secret`, `scanner_license_key`, `token`, `payload.*`.
  - `backend/app/main.py::sentry_sdk.init` — add `with_locals=False`.
  - `web/src/instrument.ts` — mirror the body-default-deny on the frontend SDK.
  - `backend/tests/test_sentry_scrubber.py` — capture `before_send` events; assert no plaintext in serialized output.
- **Effort**: ~½ day.

### 3. fix(stock): close `add_stock` + `release_reservations` advisory-lock holes; align trigger NULL bucket with service — ✅ Submitted as [PR #28](https://github.com/matescb/stockManager/pull/28) (CI green, no ops step)

- **v2 IDs**: `BE2-001` + `BE2-008` (Critical/High) + `DB-002` (High) + `BE-002` (v1 follow-up) + `TEST-005` (regression net)
- **v1 IDs**: BE CRIT-1 (final close), BE-002
- **Files**:
  - `backend/app/domain/stock/service.py::add_stock` — first statement must be `_lock_for_stock_write(...)`.
  - `backend/app/domain/orders/service.py::receive` — same.
  - `backend/app/domain/builds/service.py::consume` (output_lot insert) — same.
  - `backend/app/domain/builds/service.py::release_reservations` + `apply_reservations` — aggregate part_ids, sort, lock each, then read/write.
  - `backend/app/domain/stock/service.py::remove_stock` / `move_stock` / `adjust_stock` — service-side validation must use the same `(part, lot, storage)` tuple the trigger groups by; reject with 400 when caller omits a coordinate that exists as a non-NULL bucket.
  - `backend/tests/test_orders_receive_concurrency.py` (new), `test_builds_consume_concurrency.py` (new), `test_stock_ledger.py` (extend with the BE-002 case).
- **Effort**: ~1 day.

### 4. feat(infra): pre-deploy `pg_dump` + post-deploy health gate; `/api/health` checks DB + uploads volume — ⏳ Pending

- **v2 IDs**: `INFRA2-001` + `INFRA2-002` (both Critical) + `INFRA-001` (v1)
- **v1 IDs**: Infra CRIT-1 (final close), Infra HIGH-1, Infra HIGH-7, Infra HIGH-8
- **Why now**: Unbundled from the off-host backup story (which the user paused). These two prereqs don't need a remote bucket and immediately change the deploy from "hope it works" to "deploy aborts on dump or health failure."
- **Files**:
  - `.github/workflows/ci.yml` deploy script — add `ssh "/srv/stockmanager/deploy/predeploy-dump.sh $(git rev-parse --short HEAD)"` before `docker compose up`; abort on non-zero.
  - `deploy/predeploy-dump.sh` (new) — `pg_dump | gzip > /srv/backups/stockmanager/pre-deploy-${TS}-${SHA}.sql.gz`; abort on failure; separate retention from the nightly dumps.
  - `backend/app/main.py::health` — `SELECT 1` against `db`, `os.access(UPLOAD_DIR, os.W_OK)`; return 503 on failure.
  - `docker-compose.prod.yml` — `healthcheck:` on backend (`curl -fsS http://127.0.0.1:8000/api/health`) and web; `depends_on: { backend: { condition: service_healthy } }` on web.
  - `.github/workflows/ci.yml` deploy script — final `for i in 1..30; do curl -fsS https://parts.matescb.cz/api/health && break; sleep 2; done; curl -fsS https://parts.matescb.cz/api/health` step; non-zero exit fails the deploy.
- **Effort**: ~1 day.

### 5. fix(security): hash session tokens at rest (auth path) — ⏳ Pending

- **v2 IDs**: `SEC2-003` (High)
- **v1 IDs**: (not in v1; net-new at `SEC-006` rename in v2 area review)
- **Files**:
  - `backend/app/core/auth.py` — mint plaintext `secrets.token_urlsafe(48)`; store `sha256(token).hexdigest()` (or HMAC keyed by `SESSION_SECRET`) in `UserSession.token_hash`; raw token only on cookie.
  - `backend/app/core/deps.py:26` — lookup by `token_hash`; compare with `hmac.compare_digest`.
  - `backend/alembic/versions/0017_session_token_hash.py` (new) — drop `users_sessions.token`, add `token_hash` column. **All existing sessions invalidated** (acceptable; no prod data of value).
  - `backend/tests/test_auth.py` — pin DB stores no plaintext; lookup constant-time.
- **Effort**: ~½ day.

After these five, the next tier is: CSRF middleware (`SEC2-001`) + `/api/workspaces/{ws}/switch` membership check (`SEC2-004`) + TanStack workspace-keyed cache (`FE2-003` + `FE2-004`) + the testing meta-fixes (`TEST-001` + `TEST-002` + `TEST-003`).

## Deferred / out-of-scope (per user direction)

| v1 ID | Title | Reason |
|---|---|---|
| Infra CRIT-1 + HIGH-9 | Backup hardening — pre-deploy dump + concurrent-run lock | `INFRA2-003` (off-host) shipped 2026-05-02 via [matescb/vps-backup](https://github.com/matescb/vps-backup) with restore drill; `INFRA2-001` (pre-deploy `pg_dump`) is already wired in CI via `deploy/predeploy-dump.sh`. `INFRA2-014` (concurrent-run lock on the backup script) remains as a sub-item — low priority since cron is the only caller and runs are 24h apart. |
| Arch CRIT-2 | `barcodeReader/` graveyard incl. suspected license blob | User direction 2026-05-01: "fake key and it probably won't be used as scanner at all." Files remain in tree but are not load-bearing. |
| Arch CRIT-3 | `web/public/scandit/*` overrides node_modules wasm | Same direction as above. Inert as long as Scandit isn't the active scanner. |

## Methodology notes

- v1 finding enumeration: `review-2026-04-30/00-master.md` Unified CRITICAL list (22 rows), and `### [HIGH-N]` headings across `01-security.md` … `05-architecture-docs.md` (38 rows total).
- PR-to-commit mapping: `git log --since=2026-04-30` (every shipped commit on `main` after the v1 review). Most commit subjects cite the v1 ID directly.
- v2 cross-ref: SUMMARY.md's "Recent commits already addressing v1 findings" list (3 entries verified by the security agent), plus per-area files' "Existing review IDs covered/extended" preambles and "extends X" / "refines Y" callouts in individual finding descriptions.
- This file is static analysis only. No runtime verification was performed against `parts.matescb.cz`.
