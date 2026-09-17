# parts

Audience: engineer

Owns the `Part` aggregate (linked / local / meta / sub-assembly), MPN uniqueness, provider lookups, asset caching, and the bag-signature normaliser used by scan-import.

## Files

| File | What |
|---|---|
| `models.py` | `Part`, `PartCadKey`, `PartDatasheet`, `PartMetaMember`, `PartSubstitute`, `BulkImportIdempotency`, `WorkspaceProviderCredential`, `PartProviderLink` |
| `schemas.py` | Pydantic request/response models for the parts API |
| `provider_fields.py` | Which custom-field keys a provider owns + the primary/secondary namespace boundary |
| `part_type.py` | Re-derives `part_type` (`linked` / `local`) from `linked_provider` at every link/unlink, and audits the change |
| `spec_schema.py` | Maps one provider spec payload onto the canonical per-category schema — `canonical` / `optional` / `catalog` / `dropped` |
| `spec_schema_tables.py` | The schema data: canonical keys + provider aliases per category, the junk denylist, the catalog key list, our-category-name → slug rules |
| `spec_schema_tables_more.py` | The same data for the common optional keys and the seven active-component classes (IC, connector, crystal, fuse, switch, transformer, mechanical) |
| `spec_key.py` | The `SpecKey` row type both table modules are written in |
| `spec_extract.py` | Per-key value transforms — one `Size / Dimension` into a length and a width, a vendor's metric equivalent out of an inch figure, an AEC-Q token out of `Ratings` |
| `spec_values.py` | SI value parser/formatter — `10 kOhms` → `(10000, "Ω", "10 kΩ")`. Never raises |
| `spec_category_map.py` | Provider category string → our category name path (`Ceramic Capacitors` → `Capacitors / Ceramic`) |
| `provider_credentials.py` | `credentials_for` / `upsert` / `clear` for per-workspace provider keys |
| `provider_links.py` | `part_provider_links` CRUD — which providers know a given part |
| `services/assets.py` | Remote asset (image / datasheet) download → content-addressed storage; owns the two fetch policies |
| `services/datasheets.py` | Local datasheet store — fetch/adopt, register as an `Attachment`, resumable backfill |
| `services/bag_signature.py` | `compute_bag_signature` — SHA-256 over normalised raw bag code |
| `services/provider_cache.py` | `lookup_with_cache` / `lookup_fresh` + per-provider circuit breaker |
| `services/provider_import.py` | Create a linked `Part` from a lookup result (part columns, assets, category, specs) |
| `services/spec_reconcile.py` | **The single writer** for a provider payload → `custom_fields`, for create AND refresh |
| `services/provider_field_values.py` | `truncate_provider_field_value` — the `custom_fields.value` cap, shared by all three writers |
| `services/spec_normalize.py` | The `spec-normalize` backfill job — batching, dry-run/apply boundary, audit, advisory lock |
| `services/spec_normalize_rows.py` | Its row-level rules: re-key, archive, never delete, never touch what a user owns |
| `services/spec_normalize_report.py` | The review CSV the backfill is approved from |
| `providers/base.py` | `PartsProvider` protocol + result types |
| `providers/mouser.py`, `providers/digikey.py` | Concrete provider clients (per-workspace creds) |

## Public surface

| Operation | Entry point |
|---|---|
| Compute bag signature | `services/bag_signature.py::compute_bag_signature` |
| Cache a provider asset | `services/assets.py::fetch_provider_asset` (`::fetch_asset` for the failure code) |
| Store a part's datasheet locally | `services/datasheets.py::fetch_datasheet_for_part` |
| Sweep parts missing a local datasheet | `services/datasheets.py::backfill_missing_datasheets` |
| MPN lookup (cached / fresh) | `services/provider_cache.py::lookup_with_cache`, `::lookup_fresh` |
| Resolve a provider's credentials | `provider_credentials.py::credentials_for` |
| Whose namespace is this key? | `provider_fields.py::provider_owns_custom_field_key` |
| Whose ROW is this? (delete / unlink) | `provider_fields.py::provider_wrote_custom_field_row` |
| May I overwrite this canonical row? | `spec_schema.py::provider_outranks` |
| Classify one provider spec payload | `spec_schema.py::normalise` |
| Write a provider payload onto a part | `services/spec_reconcile.py::reconcile_provider_specs` |
| File an uncategorized part | `services/spec_reconcile.py::apply_provider_category` |
| Create a linked part from a lookup | `services/provider_import.py::create_from_provider_lookup` |
| What is this category missing? | `spec_schema.py::missing_mandatory` |
| Re-key a part's legacy provider rows | `services/spec_normalize_rows.py::normalize_part_rows` |
| Back-fill the whole table | `services/spec_normalize.py::normalize_specs` (`run_job spec-normalize`) |
| Re-parse a value already under its canonical key | `spec_schema.py::canonical_value` |
| Our category name → schema slug | `spec_schema.py::category_slug_for` |
| Provider category → our category path | `spec_schema.py::category_for_provider` |
| Parse a spec value | `spec_values.py::parse_si`, `::format_si` |
| Take part of a value for one key | `spec_extract.py::extract_for` |
| A part's provider links | `provider_links.py::links_for_part`, `::upsert_link` |
| Refresh one part from one provider | `services/provider_refresh.py::refresh_part` (the route AND the job) |
| Sweep a workspace's linked parts | `services/provider_refresh_job.py::refresh_linked_parts` (`run_job provider-refresh`) |
| Keep `part_type` in step with the link | `part_type.py::sync_part_type_and_log` |

## Hard rules (this module)

1. **MPN uniqueness is per-workspace.** Partial unique index `uq_parts_ws_mpn` (`WHERE mpn IS NOT NULL AND archived_at IS NULL`). Create-part returns `409` with `existing_id`+`existing_name`. See [ADR-0004](../../../../docs/adr/0004-mpn-uniqueness-per-workspace.md).
2. **Assets are content-addressed.** Stored at `{UPLOAD_DIR}/parts/{ws_id}/{sha}.{ext}`; URL is `/api/parts/assets/{ws_id}/{filename}`. See [ADR-0005](../../../../docs/adr/0005-content-addressed-assets.md).
2b. **The datasheet backfill fetches without a host allow-list; the resolved IP is pinned instead.** `services/assets.py` drops `_ALLOWED_HOSTS` only when BOTH `kind` is in `_UNRESTRICTED_KINDS` (`datasheet`) AND the caller passes `allow_any_host=True`. Only `services/datasheets.py` (the cron backfill) opts in; `fetch_provider_asset` — what the routes call — never does. What carries the weight: resolve once via `_resolve_pinned_ip`, refuse the host if *any* answer fails `is_global`, then connect to that IP literal with `Host:` and the `sni_hostname` extension keeping the real name, so httpx never re-resolves. Plus HTTPS-only, `follow_redirects=False`, no `user:pass@` URLs, the 10 MB streaming cap, a 45s wall-clock budget, magic-byte validation, PDF-only, and a real `User-Agent` (httpx's default is 403'd by Akamai-fronted vendors — ADR-0033 postscript). The per-host throttle is gated on the same opt-in — it is a blocking sleep and must never reach a request handler. See [ADR-0033](../../../../docs/adr/0033-datasheet-fetch-drops-host-allow-list.md).
3. **One primary provider, many secondaries, disjoint namespaces — except canonical specs.** The primary (`workspaces.parts_provider`) owns the part columns and the un-namespaced `source='provider'` catalog rows. A secondary writes no part column and only `"{provider}:"`-prefixed catalog/optional fields. **Both** write un-namespaced CANONICAL spec keys (`resistance`), so ownership of those is `custom_fields.provider` rather than the key prefix, and a contested key goes to the higher `spec_schema.PROVIDER_PRECEDENCE` (DigiKey > Mouser). `provider_fields.py::provider_wrote_custom_field_row` is where both branches live; writing is the looser `spec_schema.provider_outranks`. See [ADR-0031](../../../../docs/adr/0031-primary-and-secondary-parts-providers.md) and [ADR-0034](../../../../docs/adr/0034-spec-schema.md).
4. **Catalog vs spec keys are split.** `web/src/lib/providerCatalog.ts` defines the FE catalog-key list. The server-side mirror is `provider_fields.py` — `PROVIDER_RESERVED_CUSTOM_FIELD_KEYS` and `PROVIDER_ASSET_CUSTOM_FIELD_KINDS`, read by `api/routes/custom_fields.py`, `api/routes/parts_refresh.py` and `app/mcp/tools/_shared.py`; the provider-side field shapes live in `providers/base.py`. Adding a catalog key needs the FE list **and** the relevant server-side touchpoint. See [ADR-0007](../../../../docs/adr/0007-provider-catalog-vs-spec-split.md).

## See also

- [Domain doc — parts](../../../../docs/domain/parts.md) — part types, archival, ER position
- [Domain doc — providers](../../../../docs/domain/providers.md) — Mouser / DigiKey
- [Domain doc — scan-import](../../../../docs/domain/scan-import.md) — `bag_signature` + MIL-STD-130N
- [API — parts](../../../../docs/api/parts.md) — REST surface (parts_core / parts_assets / parts_refresh / parts_scan / parts_provider)

## Don't

- Don't change the bag-signature normalisation order in `services/bag_signature.py` — `web/src/lib/bagCode.ts` mirrors it; signatures are the only stable correlation key for re-scans (ADR-0006).
- Don't add a new "catalog" custom-field key without updating `web/src/lib/providerCatalog.ts` AND verifying the server-side mirror (issue #314 tracks finding/landing the canonical server location). The Specs vs Sourcing tabs split on this list.
- Don't bypass `services/provider_cache.py::lookup_with_cache` from a route — the circuit breaker lives there.
- Don't widen a provider reconciliation past `provider_wrote_custom_field_row`. Its trailing pass deletes every `source='provider'` row absent from the payload; unscoped, that eats every other provider's rows on each refresh. `tests/test_secondary_provider.py` pins both directions.
- Don't apply the `custom_fields.provider` test to NON-canonical keys. It is the mirror-image bug: a workspace that switched primary could never prune the old primary's bare rows, and a payload containing one would collide with it on `uq_cf_unique`.
- Don't write specs from a route. `services/spec_reconcile.py` is the only writer, so there is one statement of what a provider payload means — there used to be two, and they drifted.
- Don't let provider import create a category. `apply_provider_category` resolves a path and gives up; a vendor taxonomy we do not control must never grow a curated tree.
- Don't let a secondary provider write a part column. `manufacturer` / `mpn` / `footprint` / `description` and `parts.linked_*` belong to the primary alone.
