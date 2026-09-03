# parts

Audience: engineer

Owns the `Part` aggregate (linked / local / meta / sub-assembly), MPN uniqueness, provider lookups, asset caching, and the bag-signature normaliser used by scan-import.

## Files

| File | What |
|---|---|
| `models.py` | `Part`, `PartCadKey`, `PartMetaMember`, `PartSubstitute`, `BulkImportIdempotency`, `WorkspaceProviderCredential`, `PartProviderLink` |
| `schemas.py` | Pydantic request/response models for the parts API |
| `provider_fields.py` | Which custom-field keys a provider owns + the primary/secondary namespace boundary |
| `provider_credentials.py` | `credentials_for` / `upsert` / `clear` for per-workspace provider keys |
| `provider_links.py` | `part_provider_links` CRUD — which providers know a given part |
| `services/assets.py` | Provider asset (image / datasheet) download → content-addressed storage |
| `services/bag_signature.py` | `compute_bag_signature` — SHA-256 over normalised raw bag code |
| `services/provider_cache.py` | `lookup_with_cache` / `lookup_fresh` + per-provider circuit breaker |
| `providers/base.py` | `PartsProvider` protocol + result types |
| `providers/mouser.py`, `providers/digikey.py` | Concrete provider clients (per-workspace creds) |

## Public surface

| Operation | Entry point |
|---|---|
| Compute bag signature | `services/bag_signature.py::compute_bag_signature` |
| Cache a provider asset | `services/assets.py::fetch_provider_asset` |
| MPN lookup (cached / fresh) | `services/provider_cache.py::lookup_with_cache`, `::lookup_fresh` |
| Resolve a provider's credentials | `provider_credentials.py::credentials_for` |
| Whose namespace is this key? | `provider_fields.py::provider_owns_custom_field_key` |
| A part's provider links | `provider_links.py::links_for_part`, `::upsert_link` |

## Hard rules (this module)

1. **MPN uniqueness is per-workspace.** Partial unique index `uq_parts_ws_mpn` (`WHERE mpn IS NOT NULL AND archived_at IS NULL`). Create-part returns `409` with `existing_id`+`existing_name`. See [ADR-0004](../../../../docs/adr/0004-mpn-uniqueness-per-workspace.md).
2. **Assets are content-addressed.** Stored at `{UPLOAD_DIR}/parts/{ws_id}/{sha}.{ext}`; URL is `/api/parts/assets/{ws_id}/{filename}`. See [ADR-0005](../../../../docs/adr/0005-content-addressed-assets.md).
3. **One primary provider, many secondaries, disjoint namespaces.** The primary (`workspaces.parts_provider`) owns the part columns and the un-namespaced `source='provider'` rows. A secondary writes no part column and only `"{provider}:"`-prefixed fields. Every reconciliation scopes itself through `provider_fields.py::provider_owns_custom_field_key`, which is what stops one provider's refresh from deleting another's rows. See [ADR-0031](../../../../docs/adr/0031-primary-and-secondary-parts-providers.md).
4. **Catalog vs spec keys are split.** `web/src/lib/providerCatalog.ts` defines the FE catalog-key list. The server-side mirror is `provider_fields.py` — `PROVIDER_RESERVED_CUSTOM_FIELD_KEYS` and `PROVIDER_ASSET_CUSTOM_FIELD_KINDS`, read by `api/routes/custom_fields.py`, `api/routes/parts_assets.py` and `app/mcp/tools/_shared.py`; the provider-side field shapes live in `providers/base.py`. Adding a catalog key needs the FE list **and** the relevant server-side touchpoint. See [ADR-0007](../../../../docs/adr/0007-provider-catalog-vs-spec-split.md).

## See also

- [Domain doc — parts](../../../../docs/domain/parts.md) — part types, archival, ER position
- [Domain doc — providers](../../../../docs/domain/providers.md) — Mouser / DigiKey
- [Domain doc — scan-import](../../../../docs/domain/scan-import.md) — `bag_signature` + MIL-STD-130N
- [API — parts](../../../../docs/api/parts.md) — REST surface (parts_core / parts_assets / parts_scan / parts_provider)

## Don't

- Don't change the bag-signature normalisation order in `services/bag_signature.py` — `web/src/lib/bagCode.ts` mirrors it; signatures are the only stable correlation key for re-scans (ADR-0006).
- Don't add a new "catalog" custom-field key without updating `web/src/lib/providerCatalog.ts` AND verifying the server-side mirror (issue #314 tracks finding/landing the canonical server location). The Specs vs Sourcing tabs split on this list.
- Don't bypass `services/provider_cache.py::lookup_with_cache` from a route — the circuit breaker lives there.
- Don't widen a provider reconciliation past `provider_owns_custom_field_key`. Its trailing pass deletes every `source='provider'` row absent from the payload; unscoped, that eats every other provider's rows on each refresh. `tests/test_secondary_provider.py` pins both directions.
- Don't let a secondary provider write a part column. `manufacturer` / `mpn` / `footprint` / `description` and `parts.linked_*` belong to the primary alone.
