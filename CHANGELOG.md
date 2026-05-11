# Changelog

Through Phase 10, each phase corresponded to a single squashed commit +
a `docs/phases/NN-*.md` document. The post-Phase-10 stream below didn't
follow that model — it's a continuous flow of product work + production
hardening landing per-commit. Themes are summarised below; `git log` is
the canonical record.

## 2026-05 — feedback brief fixes

- **SX-10 / #485** Project Sourcing capacity now shows cost per single BOM
  alongside total BOM cost and short-quantity price to pay.
- **SX-12 / #487** Project Sourcing now uses the four-level TrustedParts
  risk vocabulary, including a light-green Low-Med band and header popover
  legends for Lifecycle and Supply chain risk columns.
- **SX-11 / #486** Project Sourcing coverage variant prices now represent the
  purchasable covered lines for the returned distributor combo, including partial
  coverage, and the UI labels partial totals as covered-line prices.
- **SX-5 / #480** Project Sourcing keeps the legacy distributor coverage matrix
  shortfall-based while the fewest-distributors variant continues to apply MOQ
  selected quantities for feasibility and totals.
- **SX-6 / #473** Project Sourcing BOM coverage now keeps TanStack Query
  display data warm for instant remounts and shows a non-blocking background
  refresh hint instead of a skeleton during refetches.
- **SX-5 / #472** Project Sourcing coverage now shows lowest-price and
  fewest-distributor combination cards above the per-distributor matrix.
- **SX-3 / #470** Project Sourcing BOM rows now open a TrustedParts distributor
  drill-down with availability text, price breaks, MOQ, packaging, RoHS data, and
  distributor links.
- **SX-4 / #471** Project Sourcing capacity now separates total BOM cost
  from the short-quantity price to pay, with `est_purchase_cost` retained as
  a deprecated alias.
- **SX-1 / #468** Project Sourcing now splits BOM lifecycle,
  supply-chain, and RoHS details into dedicated columns, hides the crowded
  lead-time column by default, and colours TrustedParts Low/Medium/High
  lifecycle risk text.
- **SX-2 / #469** Project Sourcing now requests the workspace currency for BOM
  coverage and exposes converted BOM offer display prices with top-level FX status.
- **TPS-5 / #452** Project Sourcing BOM rows and the Sourcing Risk report now
  flag TrustedParts lifecycle-risk text, supply-chain-risk text, tariff
  exposure, and EU RoHS non-compliance from the TPS-4 gap fields.
- **TPS-10 / #457** Workspace sourcing settings can now store an optional
  TrustedParts `LanguageCode` for specification translations.
- **TPS-4 / #451** TrustedParts gap-field parsing now surfaces lifecycle
  risk, supply-chain risk, tariff status, manufacturer id, specifications,
  distributor id, RoHS compliance, availability text, quantity multiple,
  formatted price amount, price text, TP current date, and TP response time
  in sourcing DTOs and route responses.
- **TPS-2 / #449** TrustedParts responses are now validated through the
  generated Inventory API v2 models before app DTO mapping; auth moved to the
  `X-Api-Key` header, deprecated `CompanyId` is no longer sent, and TP
  `ErrorMessage` bodies now surface as upstream errors instead of empty results.
- **FB-007 / #437** Active sourcing lists now backfill saved workspace
  sourcing defaults: `active_distributors` is unioned with preferred
  distributors, and saved country/currency values are appended when missing.
  Project Sourcing also defaults distributor filters to the saved/active
  intersection before falling back to the first active distributor.
- **FB-003c / #412** Sourcing capacity now prices the requested build
  quantity when after-purchase capacity floors to zero, fixing missing
  `est_purchase_cost` values at low build quantities.
- **FB-003d / #413** Project Sourcing BOM rows now render the per-row
  lead time returned by the Source-BOM response.

## 2026-05 — security follow-ups

- **SEC2-013 / #72** Invitation accept flow switched to constant-time
  HMAC comparison.  Previously the accept endpoint queried
  `WHERE token_hash = $digest` — a timing oracle because SQL string
  equality is not constant-time.  Fix: `token_hmac` column added to
  `workspace_invitations` (migration 0021, HMAC-SHA-256 keyed on
  `SESSION_SECRET`).  Accept now looks up by `id` (PK, no timing
  oracle) then calls `hmac.compare_digest(hmac_of_supplied, row.token_hmac)`.
  Token returned by the create endpoint is now a composite
  `"{id}:{plaintext}"` string so the frontend passes the PK opaquely.
  **Operator note:** existing pending invitations are invalidated by
  this migration (plaintexts were never stored, so `token_hmac` cannot
  be backfilled).  Revoke and re-issue any outstanding invitations
  after deploying.

## 2026-05 — teardown follow-ups

- **DB-009 / #100** Corrected the `Revision ID:` docstring header in
  `0001_initial.py` (was `2a3353f8b5fe`) and
  `0005_workspace_invitations.py` (was `24ac5d07a692`) to match the
  canonical `revision = '0001'` / `'0005'` constants Alembic actually
  reads. Comment-only edit; `alembic upgrade head` is unchanged. Now
  `git grep` and `alembic show <id>` agree.

## 2026-05 — security remediation (PRs #1 – #9)

Bulk close-out of the 2026-04-30 review (`review-2026-04-30/`,
22 CRITICAL + 38 HIGH findings). PR numbers and merge order:

### Workspace isolation (Tier A)
- **#1 / #2** Cross-workspace FK leaks closed across attachments,
  projects, custom_fields, tags, stock, builds, and `parts.default_storage_location_id`. New shared `assert_in_workspace` /
  `assert_polymorphic_in_workspace` helper in `app/api/_helpers.py` —
  one canonical replacement for the `db.get(Model, id) + manual
  workspace_id check` pattern. `CustomFieldIn.source` dropped from
  the schema (was client-controllable; would let callers forge
  `source='provider'` rows).

### Hardening batch (Tier B)
- **#3** Workspace cookie hardened (httponly + secure-in-prod +
  samesite=lax). Backend container runs as `appuser` uid 1000
  (gosu + idempotent /data chown on boot). `.dockerignore` at repo
  root and `web/` drops build context from 263 MB to ~10 MB and
  stops shipping `.env` into the daemon. `web/Dockerfile.prod` now
  strips `*.map` from the served image (Vite's "hidden" sourcemaps
  go to Sentry only). `/api/docs` / `/redoc` / `/openapi.json`
  disabled in prod. Sentry `before_send` scrubs request body on
  workspace settings PATCH/switch and strips Cookie /
  Authorization / X-Workspace-Id headers — frontend has the
  matching `beforeSend`. All GitHub Actions SHA-pinned with
  `permissions: contents: read` at workflow scope and
  `environment: production` on the deploy job.

### Tier C / D — concrete CRITs
- **#4** Attachment XSS hardening: MIME allow-list (PNG/JPEG/WebP/PDF;
  SVG explicitly excluded), magic-byte sniff defeats `evil.html as
  image/png`, declared-vs-actual MIME mismatch rejects, filename
  sanitised to `[A-Za-z0-9._-]{1,80}` with extension derived from
  validated MIME, `Content-Disposition: attachment` always forced
  on download. New `MAX_UPLOAD_BYTES` config (default 10 MiB);
  upload reads at most `MAX + 1` and 413s the rest. Legacy
  pre-allow-list attachments fall back to `application/octet-stream`
  on download.
- **#5** `/api/sentry-tunnel` rate-limited (`60/min/IP`) and
  body-capped via streaming read (`SENTRY_TUNNEL_MAX_BYTES` default
  200 KiB) — was an open ingress that anyone could pump bytes
  through. DSN allow-list preserved.
- **#6** `bulk_import_from_scan` per-row savepoints. Each row's
  writes wrap in `with db.begin_nested():`; an exception inside
  rolls back only that row. Outer transaction commits surviving
  savepoints. New `row_failed` outcome in the per-row response.
  Provider-call exceptions now `sentry_sdk.capture_exception` —
  preserves row-resilience, makes ops aware.
- **#7** `default_storage_mandatory` bypass closed — predicate
  short-circuited on `storage is None`; now rejects both
  "wrong storage" and "no storage at all" when the part requires
  it.
- **#8** Build consume aggregates demand per `(part, lot, storage)`
  before the per-line check. Two BOM entries claiming 60 each of
  the same 100-piece reel now fail with `have 100, want 120`
  before any `-60` row is written.

### Tooling additions
- `CLAUDE.md` at repo root pins the project's hard invariants
  (append-only ledger, code-enforced workspace isolation, response
  envelope shape, content-addressed asset URLs, `bag_signature`,
  prod-deploy footguns).
- `.claude/` adds project-scoped Claude Code config: hooks for
  pre-edit alembic-migration guard + post-edit pytest-collect +
  end-of-turn `tsc -b`; subagent contracts for the
  `workspace-isolation-checker` and `alembic-migration-reviewer`
  reviewer flows; `settings.json` wires it all to the lifecycle
  events.

### Test footprint
- 224 backend tests passing (was 151 at the start of the
  remediation). Major additions: workspace-isolation matrix across
  every router, attachment allow-list / size-cap / sanitization,
  Sentry-tunnel rate-limit + body-cap, `default_storage_mandatory`
  bypass regression, build-consume aggregation regression,
  bulk-import savepoint regression, security-hardening cookie +
  Sentry-scrubber pins.

## Beyond Phase 10 — production deployment, observability, scan-to-import

### Production live at `parts.matescb.cz`
- VPS-hosted docker-compose stack behind the host's existing Apache 2.4
  + certbot. New apps follow the same pattern (`deploy/parts.matescb.cz.conf`)
  as siblings on the host. Web container's nginx handles `/api/*` →
  backend internally so Apache only ProxyPasses one host port.
- GitHub Actions CI: `backend-tests` (pytest + postgres service container),
  `web-build` (npm ci + vitest + tsc + vite build), `deploy` (SSH to VPS,
  `git reset --hard` + `docker compose up -d --build`). Auto-deploys every
  push to `main` after green tests.
- Nightly `deploy/backup.sh` — pg_dump + uploads tar to `/srv/backups/`
  with 30-day retention.
- Session cookie marked `Secure` when `APP_ENV=prod`; auth `/login` and
  `/signup` rate-limited via slowapi (10/min/IP and 5/hour/IP). uvicorn
  runs `--workers 1` so the in-process bucket store is global.

### Observability — Sentry on both runtimes
- Backend (`sentry-sdk[fastapi]`) and frontend (`@sentry/react`) wired
  via env-driven DSNs. Frontend init lives in a sidecar `instrument.ts`
  imported first per the official sentry-react-sdk skill; React Router
  v6 hooks-based browser-tracing integration, Session Replay with
  `maskAllText` + `blockAllMedia`.
- Same-origin `/api/sentry-tunnel` forwards envelopes through our backend
  so ad-blockers (uBlock, Brave Shields, Pi-hole) don't drop events with
  `ERR_BLOCKED_BY_CLIENT`. Allow-list pinned to the configured
  `SENTRY_DSN` + `VITE_SENTRY_DSN` so it can't be abused as an open
  forwarder.
- `sentryVitePlugin` uploads hidden source maps at build time + tags
  every release with the deploy's git SHA. Sentry groups issues per
  release and auto-resolves them when the next release deploys.

### Provider expansion — Mouser then DigiKey
- Mouser `ProductAttributes` → `specs[]` + image_url persisted as
  `custom_fields` with `source='provider'`. Description-mining picks
  the parametric values out of the prose (resistance, capacitance,
  package, tolerance, …). `Specs` tab on part detail; provider
  source attribution + manual override + refresh-from-provider
  (non-destructive reconciliation).
- DigiKey Product Information V4 alongside Mouser. 2-legged OAuth
  (`client_id` + `client_secret` — second workspace credential column
  in alembic 0009). ProductDetails first; on 404 falls back to keyword
  search (handles distributor-side MPN normalisation, e.g. Molex
  `98266-0897` indexed as `0982660897`). One-shot 401 retry on token
  rotation. DigiKey's `Parameters[]` reliably populates, so most
  parts come back with a real parametric table.
- Workspace settings page picks the provider + stores its credentials.
  Mouser's "Invalid unique identifier" key-rejection is translated to
  an actionable "Re-paste a valid key in Settings…" message.

### Scan-to-import bulk flow
- New `/parts/scan-import` route: scan a stack of bags, see each
  provider lookup land in real time, then import them in one go.
- MIL-STD-130N / ANSI MH10.8.2 bag-code parser (`web/src/lib/bagCode.ts`)
  extracts MPN (1P), quantity (Q), date code (10D/9D), lot (1T),
  serial (1S), order/PO/invoice references (K/1K/14K/11K),
  manufacturer (1V). Three passes — real separators, inline-DI regex,
  plain-MPN fallback for 1D barcodes. Normalises ZXing's printable
  Control-Pictures block (U+241C–U+241F, U+2404, U+2420) back to ASCII
  control chars before parsing.
- Bag traceability persists: `lot_name` (synthesised "Lot X · DC Y")
  creates a Lot row, order/invoice refs become the `stock_entry`
  comment so a physical bag traces back to its source PO months
  later. Scanned quantity always lands on-hand even without a
  storage location.
- Vitest pins down every parser regression we've shipped a fix for.

### Switchable scanner backend (alembic 0010)
- Workspace setting picks `zxing` (royalty-free default) or `scandit`
  (opt-in, requires a workspace-scoped license key). `Scanner.tsx` is
  a thin lazy-import dispatcher; both backends conform to one props
  contract so call sites don't change.
- ZXing-C++ wasm copied from `node_modules` to `public/zxing/` at
  build time so we serve it from our own origin (not the package's
  default jsDelivr CDN). `Cache-Control: public, immutable` on the
  wasm route.
- Camera picker (multi-camera phones) + zoom slider. Hardware mode
  via `track.applyConstraints({ advanced: [{ zoom }] })` when
  available; digital fallback (centre-cropped frame fed to the decoder
  at native resolution + CSS `transform: scale()` on the preview)
  for cameras that don't expose hardware zoom — PC webcams, Firefox.
  Audible click + haptic vibration on each successful read.
  Permission-denied UX with a "Try again" button instead of cryptic
  "Camera API unavailable".
- `/parts/scan` was a single-MPN-lookup page; consolidated into
  `/parts/scan-import` (its duplicate-detection path subsumes the
  lookup case). Old route redirects.

### Engineering hygiene
- Pydantic v1 `class Config:` blocks → `ConfigDict`. `httpx` promoted
  to a runtime dependency (was dev-only; broke first lookup-mpn after
  the prod image was built without `[dev]`).
- Lazy-routed orders/builds/reports/projects/settings — main bundle
  drops from 487 KB → 423 KB. `@sentry/react` carved into its own
  `manualChunks` split so it caches independently.
- vitest + bagCode regression suite added to CI.

## Phase 10 — RBAC + workspace invitations
- Roles enforced (`owner | admin | member | viewer`); `require_role()`
  dependency factory.
- `workspace_invitations` table + token-based accept flow.
- Members & invitations UI in workspace settings; accept-invite UI on
  account page.
- Migration 0005.

## Phase 9 — Serial tracking
- `parts.serialized` boolean (migration 0004).
- `Workspace.serial_tracking_enabled` toggle now enforced on
  `add_stock` and `receive`: qty=1, `serial_number` required.
- Workspace settings UI editable; serial inputs surfaced on
  add-stock and order-receive forms.

## Phase 8 — Meta-parts & sub-assemblies
- CRUD for `PartMetaMember` rows.
- Build engine considers meta-part members the same way it considers
  registered substitutes — meta-part BOM lines now build correctly.
- `Members` tab on the part detail when `part_type='meta'`.

## Phase 7 — BOM import presets
- CRUD on `bom_import_presets`.
- Import wizard now has Save / Load / Manage preset controls.

## Phase 6 — Reports
- `/api/reports/low-stock`, `stock-value`, `bom-shortage`,
  `expiring-lots`.
- `/reports` page with sub-tabs; all CSV-exportable.

## Phase 5 — Builds & consume-from-BOM
- `builds` table (migration 0003).
- `shortage_analysis()` + `consume()` services; ledger rows tagged
  `build_consume` / `build_produce`; output sub-assembly lot when
  the project has `associated_subassembly_part_id`.
- `/builds` UI with auto-fill consumption planner.

## Phase 4 — Purchase orders
- `orders` and `order_entries` tables (migration 0002).
- Line-level receive flow → `source_type='purchase'` lots, ledger
  rows tagged `order_id` / `order_entry_id`.
- `/orders` UI with line editor + receive form.

## Phase 1–3 (initial commit)
- Auth + workspaces (argon2, cookie sessions, multi-tenant).
- Parts (CRUD, archive, scan, substitutes, meta-member table).
- Storage locations.
- Append-only stock ledger; lots with split / parent-lot.
- Projects, BOM CRUD, full CSV import wizard with mappable columns.
- Cross-cutting: attachments, custom fields, tags, global search.
- Migration 0001 (autogenerated; deferred FK on
  `projects.associated_subassembly_part_id` breaks the
  parts↔projects cycle).
