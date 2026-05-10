# ADR-0022: TrustedParts schema version pinning

Audience: engineer

- **Status**: Accepted
- **Date**: 2026-05-10
- **Supersedes**: —
- **Superseded by**: —

## Context

TrustedParts Inventory API v2 is an external contract. The current client still
uses hand-written DTOs, while follow-up work will use generated Pydantic models
to reduce drift from the official OpenAPI schema. Without a bundled schema and a
CI drift gate, generated code can silently lag behind the upstream Swagger UI.

TrustedParts publishes the v2 Swagger UI from the official API host, and the UI
loads `inventory-api-v2/swagger.json` (`Makefile:3`). The generated models live
under the sourcing domain because ADR-0020 keeps procurement sourcing separate
from catalog enrichment.

The pinned v2 schema confirms a TrustedParts lead-time limitation: it exposes only
`/v2/search`, and `StockInfo` contains unstructured `Availability` text plus
`QuantityOnHand`; it does not expose a structured `LeadTime` field or a separate
lead-time endpoint. The sourcing adapter therefore keeps `lead_time_days=None` for
TrustedParts offers unless a future schema refresh introduces a real field.

## Decision

Bundle the official TrustedParts Inventory API v2 OpenAPI document at
`docs/schemas/trustedparts-v2.json`, formatted deterministically with `jq
--sort-keys`. Generate Pydantic v2 models into
`backend/app/domain/sourcing/_generated/trustedparts_v2.py` with
`datamodel-code-generator` and a fixed AUTO-GENERATED header.

CI runs a deterministic `tp-schema-drift` job that regenerates the models from
the bundled schema and fails if the checked-in generated files change. CI does
not fetch the live TrustedParts schema; operators refresh the bundle explicitly
with `make refresh-tp-spec`.

## Consequences

- **Good**: Schema changes are explicit in PR diffs, generated code is
  reproducible, and issue #449 can integrate typed TrustedParts DTOs without
  also establishing the schema supply chain.
- **Trade-offs**: Refreshing requires an explicit operator action, so upstream
  schema changes are discovered when someone runs `make refresh-tp-spec` rather
  than by a live CI fetch. The generated file is intentionally verbose and
  reviewable as a committed artifact.
- **What it forbids**: Hand-editing generated TrustedParts models, regenerating
  from an unofficial schema URL, or wiring the generated models into
  `client.py` before the client-integration PR.

## Alternatives considered

- **Generate directly from the remote URL in every developer workflow** —
  rejected because local model regeneration would not show which upstream schema
  was reviewed and committed.
- **Fetch the live schema in CI** — rejected because CI would depend on
  TrustedParts availability and stable network access instead of the bundled
  source-of-truth artifact.
- **Check in only the schema, not generated models** — rejected because import
  and header drift would not be caught before the integration PR.
- **Vendor a manually edited schema subset** — rejected because it loses the
  official OpenAPI contract and makes upstream drift harder to audit.

## References

- Source: `Makefile:3`
- Source: `docs/schemas/trustedparts-v2.json`
- Source: `backend/app/domain/sourcing/_generated/trustedparts_v2.py`
- Source: `.github/workflows/ci.yml`
- Related ADR: [ADR-0020](0020-trustedparts-sourcing-provider-split.md)
- Issue: `https://github.com/matescb/stockManager/issues/448`
