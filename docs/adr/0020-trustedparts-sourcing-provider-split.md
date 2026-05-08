# ADR-0020: TrustedParts sourcing provider split

Audience: engineer

- **Status**: Accepted
- **Date**: 2026-05-08
- **Supersedes**: —
- **Superseded by**: —

## Context

Catalog enrichment providers and procurement sourcing answer different questions. Mouser/DigiKey lookup fills part metadata and provider-shaped specs; TrustedParts sourcing returns authorized distributor stock, price, and purchase links that are time-sensitive and subject to stricter attribution and retention rules. The Phase 1 TrustedParts work introduced credentials on `Workspace`, a short-lived sourcing cache, and a process-local parts-count budget.

TrustedParts is the first sourcing provider because its Inventory API v2 is built around authorized distributor availability and exposes a batch search endpoint for up to 50 query tokens. Its terms require clear TrustedParts attribution, prohibit indistinct aggregation with third-party content, and allow ECIA to limit or throttle API usage. TrustedParts API v2 documents the `/v2/search` endpoint at `https://api.trustedparts.com/v2/search`; the API Terms of Use require attribution and forbid uses that aggregate TrustedParts content without distinction.

## Decision

Sourcing is a separate domain under `backend/app/domain/sourcing/`, not a third `parts_provider` implementation. Workspace sourcing credentials stay on `Workspace`, are encrypted at rest, and are decrypted only in `make_sourcing_provider()` before constructing `TrustedPartsClient` (`backend/app/domain/sourcing/factory.py:12`). `POST /api/sourcing/search` calls the sourcing service facade (`backend/app/domain/sourcing/service.py:39`) and returns an attributed API-envelope response (`backend/app/api/routes/sourcing.py:87`).

The cache is workspace-scoped and short-lived. The database enforces a maximum seven-day retention window (`backend/app/domain/sourcing/models.py:13`), while the service currently uses a 30-minute TTL (`backend/app/domain/sourcing/service.py:24`). Expired-row cleanup is periodic-job infrastructure owned by ADR-0021. The budget is a parts-count budget, not a request-count budget, and is in-process by design while production runs one uvicorn worker (`backend/app/domain/sourcing/budget.py:104`). The client payload does not send `SourceIp`; user attribution is via the app user/session and visible API response attribution, not via TrustedParts `SourceIp`.

Purchase plans follow the same retention rule because they hold TrustedParts offer
snapshots while users review optimizer output. `purchase_plans` enforces
`expires_at <= created_at + interval '7 days'`, and the existing sourcing sweep job removes
expired plan rows alongside cache rows (`backend/alembic/versions/0039_purchase_plans.py:68`,
`backend/app/domain/sourcing/cache.py:95`).

TrustedParts results must stay visibly distinct from catalog-provider data. Public or UI surfaces that combine distributor data from multiple origins need an explicit future decision before launch.

## Reports policy: no persistent price history

Reports may use TrustedParts prices only as a transient request-time input. The replenishment-cost report recomputes replacement cost from existing on-hand lot costs and the short-lived sourcing cache on each request (`backend/app/domain/reports/service.py:39`); it must not add a report-specific table, column, or long-lived price snapshot because that would turn cached TrustedParts offers into persistent price history.

## Consequences

- **Good**:
  - Catalog enrichment and procurement sourcing have separate ownership boundaries, DTOs, cache policy, and ToU controls.
  - ToU constraints are enforced by construction: short retention, workspace-scoped credentials, visible attribution, no `SourceIp`, no silent mixing with other distributor sources.
  - The route's 50-MPN schema limit aligns with TrustedParts batch shape and the existing scan-import cap.
- **Trade-offs**:
  - The split adds a new domain, migrations, service facade, tests, and docs rather than reusing the older `parts_provider` adapter surface.
  - The process-local budget assumes `uvicorn --workers 1`; increasing workers needs a Redis-backed budget or equivalent shared counter first.
- **What it forbids**:
  - Permanent price history or long-retention TrustedParts offer snapshots.
  - Public mixing of TrustedParts distributor data with other-source distributor data without prior written approval / a new ADR.
  - Partial-match behavior in batch requests. Batch sourcing searches must remain exact-match.

## Alternatives considered

- **Add TrustedParts as a third `parts_provider` choice** — rejected because sourcing answers procurement/offer questions, not catalog/spec enrichment. Reusing `parts_provider` would blur credential semantics, cache TTLs, attribution, and UI ownership.
- **Fully RPC-cached sourcing layer** — rejected because long-lived cached offer history conflicts with TrustedParts retention and attribution constraints. A short-lived workspace cache is enough to reduce repeated clicks without becoming a price-history store.

## References

- TrustedParts Inventory API v2: `https://www.trustedparts.com/docs/api/trustedparts-api/version-2/`
- TrustedParts Inventory API Terms of Use: `https://www.trustedparts.com/docs/api/trustedparts-api/terms-of-use/`
- Source: `backend/app/domain/sourcing/factory.py:12`
- Source: `backend/app/domain/sourcing/service.py:39`
- Source: `backend/app/api/routes/sourcing.py:87`
- Related ADR: [ADR-0007](0007-provider-catalog-vs-spec-split.md)
- Related ADR: [ADR-0021](0021-periodic-jobs-scheduler.md)
- Issue: `https://github.com/matescb/stockManager/issues/329`
- Plan: `/home/matyas/.claude/plans/read-all-the-documentation-robust-giraffe.md`
