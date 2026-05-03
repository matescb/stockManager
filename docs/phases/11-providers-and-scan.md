# Phase 11 — Providers & scan-to-import

Audience: engineer

Wires Mouser and DigiKey as parts providers, ships the
`/parts/scan-import` flow that turns a stack of physical bags into
ledger rows, and adds the bag-signature + idempotency machinery that
keeps the flow safe against double-imports.

## Why

- Manual data entry for parametric specs (resistance, capacitance,
  package, tolerance, …) doesn't scale. A provider lookup by MPN
  populates 90% of a part's metadata for free.
- Receiving a bag of components is a physical event with a paper
  label. The label encodes everything we need (MPN, qty, lot, date
  code, PO ref) — we just have to scan and parse it.
- Re-scanning the same physical bag a week later must not silently
  double-add stock; we need a stable correlation key (the bag
  signature) and an idempotency cache for retries.

## What shipped

- **Mouser provider** — `ProductAttributes` → `specs[]` + `image_url`
  persisted as `custom_fields` rows with `source='provider'`. Source:
  `backend/app/domain/parts/services/provider.py`.
- **DigiKey provider** — Product Information V4, 2-legged OAuth.
  Schema: workspace credential columns added by
  `backend/alembic/versions/0007_workspace_parts_provider.py` (key)
  and `0009_workspace_parts_provider_secret.py` (client secret —
  DigiKey needs both). Encrypted at rest by
  `0016_encrypt_workspace_secrets.py`.
- **Catalog vs spec split** — `web/src/lib/providerCatalog.ts`
  enumerates which custom-field keys are catalog metadata (price,
  stock, manufacturer URL) vs user-curated specs; the same list is
  mirrored in `backend/app/domain/parts/services/provider.py`. The
  Specs and Sourcing tabs split on this boundary.
- **Bag-code parser** — MIL-STD-130N / ANSI MH10.8.2 with three
  passes (real separators → inline-DI regex → plain-MPN fallback).
  Extracts MPN (1P), qty (Q), date code (10D/9D), lot (1T), serial
  (1S), PO/invoice refs (K/1K/14K/11K), manufacturer (1V).
  Normalises ZXing's printable Control-Pictures block
  (U+241C–U+241F, U+2404, U+2420) back to ASCII control chars.
  Source: `web/src/lib/bagCode.ts`. Vitest pins every regression
  fix shipped to date — `web/src/lib/bagCode.test.ts`.
- **`bag_signature` column** on `stock_entries` — SHA-256 of the
  normalised raw bag code. Migration:
  `backend/alembic/versions/0012_stock_entries_bag_signature.py`.
  Partial index in `0020_partial_bag_signature_index.py`.
  Re-scanning a bag matches the same signature, surfacing the
  inline "Found bag" affordance instead of silently double-adding.
- **`/parts/scan-import` route** — bulk flow with per-row
  savepoints (each row's writes wrap in `db.begin_nested()`; one
  failure rolls back only that row). Provider-call exceptions go
  through `sentry_sdk.capture_exception`. Closed Sec CRIT-6.
- **`bulk_import_idempotency` table** —
  `backend/alembic/versions/0034_bulk_import_idempotency.py`
  (revision `0034`, BE2-003). Composite PK `(workspace_id, key)`;
  supporting index on `(workspace_id, created_at)` for the 24-hour
  TTL sweep. `key` is a client-supplied UUID4 or a server-derived
  SHA-256 of the row payload; `result_json` stores the full API
  envelope verbatim so a cache hit returns without re-running any
  provider call.
- **Switchable scanner backend** —
  `backend/alembic/versions/0010_workspace_scanner.py`. Workspace
  setting picks `zxing` (royalty-free default) or `scandit` (opt-in,
  requires a workspace-scoped license key).

## Invariants introduced

- **`bag_signature` is the only stable correlation key for a
  re-scan.** If you touch `web/src/lib/bagCode.ts`, keep the
  normalisation order the same — see `CLAUDE.md`.
- **Bag traceability persists end-to-end.** A scanned bag produces a
  `Lot` row with synthesised name (`Lot X · DC Y`) and a
  `stock_entry` whose `comments` carry the order/invoice refs. The
  physical bag traces back to its source PO months later.
- **Bulk-import is row-resilient.** Per-row savepoints and the
  idempotency cache mean a flaky provider call or a network blip
  cannot poison a whole import. Outcomes per row are explicit
  (`imported / matched / failed / row_failed`).
- **Provider secrets are workspace-scoped and encrypted at rest.**
  See [`docs/runbooks/secret-rotation.md`](../runbooks/secret-rotation.md)
  and migration 0016.

## Things deferred

- A third provider (TrustedParts is allow-listed via
  `WebFetch(domain:www.trustedparts.com)` but not yet wired —
  see `CLAUDE.md`).
- Server-side bag-code parsing — currently the parser lives only on
  the client.
- Background re-sync of provider data — refresh is on-demand from
  the part Specs tab.

## References

- Migrations: `0007`, `0009`, `0010`, `0012`, `0016`, `0020`,
  `0034`.
- Backend: `backend/app/domain/parts/services/provider.py`,
  `backend/app/api/routes/parts_provider.py`,
  `backend/app/api/routes/parts_scan.py`.
- Frontend: `web/src/lib/bagCode.ts`,
  `web/src/lib/providerCatalog.ts`, `web/src/routes/parts/`
  (`scan-import`).
- Changelog: `CHANGELOG.md` — "Provider expansion" + "Scan-to-import
  bulk flow" + "Switchable scanner backend".
- Hard invariants: `CLAUDE.md` — `bag_signature`, content-addressed
  assets, provider catalog vs spec keys.
- Notable follow-up PRs: #173/#210 (per-workspace rate limit on
  provider lookups), #263/#290 (provider-asset magic-byte sniff
  hardening — reject SVG and unknown content-types).
- TODO(verify): exact route paths for `/api/parts/bulk-import-from-scan`
  in `backend/app/api/routes/parts_scan.py`.
