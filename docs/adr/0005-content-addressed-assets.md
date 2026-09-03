# ADR-0005: Content-addressed asset storage

Audience: engineer

- **Status**: Accepted (retro-documented from existing code)
- **Date**: 2026-05-03
- **Supersedes**: —
- **Superseded by**: —

## Context

Provider lookups (Mouser, DigiKey) return part metadata with image and datasheet URLs that point at the provider's CDN. Hot-linking those URLs into the UI works until the CDN rotates the path, the provider deletes the asset, the network is slow, or the SPA is opened from a network that can't reach the CDN. Each of those cases turns a part page into a broken-image render.

Caching the assets locally fixes that, but introduces three sub-problems: where to put them, what to name them, and how to serve them safely. Naming by `(part_id, kind)` couples the file to a mutable parent; naming by upstream URL leaks third-party domain structure into our paths and breaks if the URL rotates. Naming by content hash decouples both.

## Decision

Provider images and datasheets are downloaded once by `domain/parts/services/assets.py::fetch_provider_asset` and stored at `{UPLOAD_DIR}/parts/{ws_id}/{sha256}.{ext}`. They're served by `GET /api/parts/assets/{ws_id}/{filename}` (route in `backend/app/api/routes/parts_assets.py:53`), with an optional `?name=` query parameter for the Save-As dialog filename.

The frontend builds the URL directly via `withDownloadName()` (`web/src/routes/parts/detail/PartInfo.tsx:27`); no server round-trip is needed to resolve "what's the URL for part X's image". The download path enforces magic-byte validation, host allow-listing, no redirects, and rejects SVG (SEC2-006 / SEC2-012).

## Consequences

- **Good**: Same content → same path, so cache headers can be `immutable, max-age=31536000`. Provider CDN rotation doesn't break us. Workspace-scoped paths mean the route can refuse cross-tenant fetches with a single equality check.
- **Trade-offs**: Storage is content-addressed, not lifecycle-aware; an asset that no part references any more is orphaned until a sweep removes it. The disk-bound design also means the upload volume must be backed up.
- **What it forbids**:
  - Don't change the URL structure `/api/parts/assets/{ws_id}/{filename}` — `PartInfo.withDownloadName` constructs URLs by string-concatenation, and a reshape would silently break every existing reference.
  - Don't store assets under a part-id-keyed path; it couples the file to a mutable parent and breaks deduplication across parts that share an upstream image.
  - Don't add SVG to the accepted MIME set — SVG is XML and can carry `<script>` payloads. The current allow-list (`backend/app/domain/parts/services/assets.py`) intentionally omits it.
  - Don't add `follow_redirects=True` to the downloader — the host allow-list is meaningless if a 30x can chase the request to anywhere.

## Alternatives considered

- **Hot-link upstream URLs** — rejected because provider CDN rotation, regional reachability, and offline-friendly UX all break. Also leaks user activity to the upstream CDN.
- **Object storage (S3 / MinIO)** with the same content-addressed key scheme — viable, and the path layout is already compatible. Rejected for now because the deploy is a single VPS with a local volume; introducing a second storage tier adds backup, IAM, and cost surface for no current benefit. The local layout maps cleanly to a future S3 backend if traffic justifies it.

## References

- Source: `backend/app/domain/parts/services/assets.py:1-30` (module docstring, hardening notes)
- Source: `backend/app/api/routes/parts_assets.py:29-122`
- Source: `web/src/routes/parts/detail/PartInfo.tsx:27` (`withDownloadName`)
- Rule: `CLAUDE.md:109-113`
