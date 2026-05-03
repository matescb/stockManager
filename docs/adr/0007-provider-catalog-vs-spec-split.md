# ADR-0007: Provider catalog vs spec key split

Audience: engineer

- **Status**: Accepted (retro-documented from existing code)
- **Date**: 2026-05-03
- **Supersedes**: —
- **Superseded by**: —

## Context

Provider lookups (Mouser, DigiKey) emit a flat `specs[]` array of key/value pairs. Some are parametric specs the user curates and trusts ("Resistance" = "10k"), others are sourcing metadata that drifts daily ("In stock (qty)" = "5,234", "Unit price (1+)" = "0.42 €"). The PartSpecs tab and the PartSourcing tab need to render these on opposite sides — but the provider doesn't tag them, and tagging at write time bakes the categorisation into history (so re-categorising later requires a migration).

Both tabs read from the same `custom_fields(source='provider')` rows. The split has to be a key-list consulted at render time.

## Decision

`web/src/lib/providerCatalog.ts` defines:

- `CATALOG_LITERAL_KEYS` — exact-match catalog keys (availability, compliance, packaging, distributor P/Ns, series).
- `CATALOG_REGEX_KEYS` — patterned keys (`/^Unit price \(\d+\+\)$/` for tier pricing).
- `RESERVED_KEYS` — `image_url`, `datasheet_url` (rendered by the layout header / Media affordances, not in either tab).

`isCatalogKey(key)` → Sourcing tab. `isSpecKey(key)` → Specs tab. Reserved keys appear in neither.

The same key list lives server-side in `backend/app/domain/parts/services/` (TODO(verify): exact filename — CLAUDE.md cites `backend/app/domain/parts/services/provider.py` but the directory contains `assets.py`, `bag_signature.py`, `provider_cache.py` — the catalog-key list may have moved to `provider_cache.py` or one of the `providers/` modules).

## Consequences

- **Good**: Re-categorising a key (e.g. promoting "Lifecycle" from Sourcing to a header pill) is one PR touching two files, no DB migration. Historical rows automatically re-render under the new categorisation.
- **Trade-offs**: Two source-of-truth files (TS + Python) must stay in sync. Adding a new catalog field needs both edits or the server- and client-side splits diverge.
- **What it forbids**:
  - Don't tag catalog-vs-spec at write time on the `custom_fields` row. The tag is a render-time lookup.
  - Don't add a new catalog-shaped field (price tier, availability, packaging) to only one of the two key lists. Both must move together.
  - Don't put `image_url` / `datasheet_url` on either tab — they're reserved and surfaced by the layout header.
  - Don't extend the regex list with permissive patterns (`.*price.*`); user-curated keys could collide and disappear from the Specs tab.

## Alternatives considered

- **Tag at write time** (add a `category` column on `custom_fields`) — rejected because re-categorising historical rows requires a migration, and provider responses don't carry the tag so we'd be inferring it once instead of every render anyway.
- **Heuristic split by value type** (numeric → spec, currency-shaped → catalog) — rejected as too brittle: "Tolerance" is `±5%`, "Lead time" is `12 weeks` — the value types overlap.

## References

- Source: `web/src/lib/providerCatalog.ts`
- Source: `backend/app/domain/parts/services/` (TODO(verify): exact file)
- Source: `backend/app/domain/parts/providers/mouser.py`, `backend/app/domain/parts/providers/digikey.py` (emit the `specs[]` rows)
- Rule: `CLAUDE.md:119-124`
