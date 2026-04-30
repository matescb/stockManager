# Changelog

Through Phase 10, each phase corresponded to a single squashed commit +
a `docs/phases/NN-*.md` document. The post-Phase-10 stream below didn't
follow that model — it's a continuous flow of product work + production
hardening landing per-commit. Themes are summarised below; `git log` is
the canonical record.

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
