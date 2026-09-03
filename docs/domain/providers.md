# Providers

Audience: engineer

Mouser and DigiKey are the two shipped parts-data providers. This page covers the per-workspace configuration model, the OAuth-2leg flow on DigiKey, the catalog vs spec key split, and the cache + circuit-breaker layer.

For the catalog/spec split decision see [ADR-0007](../adr/0007-provider-catalog-vs-spec-split.md).

## Primary vs secondary

A workspace configures **one primary** provider and any number of **secondaries**.

| | Primary | Secondary |
|---|---|---|
| Named by | `workspaces.parts_provider` | any other `KNOWN_PROVIDER_NAMES` entry with credentials |
| Credentials | legacy `parts_provider_api_*` columns (or a `workspace_provider_credentials` row) | `workspace_provider_credentials` row |
| Part columns (`manufacturer`, `mpn`, `footprint`, `description`) | owns them | writes none |
| `parts.linked_*` | owns them | writes none |
| Custom fields | un-namespaced (`Resistance`, `source_url`) | `"{provider}:"` prefixed (`mouser:Resistance`) |
| Link row | `part_provider_links` | `part_provider_links` |
| Scan-import, lookup-mpn | yes | no |

Each refresh reconciles only its own namespace — `backend/app/domain/parts/provider_fields.py::provider_owns_custom_field_key` is the boundary, and the reason a DigiKey refresh cannot delete the `mouser:` rows. See [ADR-0031](../adr/0031-primary-and-secondary-parts-providers.md).

**The two credential stores are separate, and no provider is in both.** The primary's key is in the legacy `workspaces.parts_provider_api_*` columns; `workspace_provider_credentials` holds secondaries only. Migration 0070 backfills nothing into it, and `PUT /api/workspaces/current/provider-credentials` returns `400 workspace.provider_is_primary` for the workspace's own `parts_provider`.

`backend/app/domain/parts/provider_credentials.py::credentials_for` is the **secondary** resolution point, not a unification of the two. It checks the `workspace_provider_credentials` row, then falls back to the legacy columns for the primary — a convenience for the single caller that has to accept either tier behind one name (the `?provider=` refresh). The primary's own flows read and decrypt the legacy columns directly, at four call sites:

| Call site | Flow |
|---|---|
| `backend/app/api/routes/parts_refresh.py` | `refresh-from-provider`, primary path |
| `backend/app/api/routes/parts_provider.py` | `lookup-mpn` |
| `backend/app/api/routes/parts_scan.py` | `bulk-import-from-scan` |
| `backend/app/domain/projects/bom_import_provider.py` | BOM import from provider |

Retiring the legacy columns requires migrating those four onto `credentials_for` first; dropping them while they still read the columns breaks the entire primary flow.

## Per-workspace credentials

Provider state lives on the `Workspace` row (`backend/app/domain/workspaces/models.py:41-47`):

| Column | Notes |
|---|---|
| `parts_provider` | `none | mouser | digikey`. Default `none`. |
| `parts_provider_api_key` | Mouser API key, or DigiKey `client_id`. Encrypted at rest via `app.core.secrets` (Sec HIGH-9). Fernet ciphertext is ~30% larger after base64; column widened to `varchar(1024)` in alembic 0016 (`backend/app/domain/workspaces/models.py:42-45`). |
| `parts_provider_api_secret` | Only DigiKey uses it (`client_secret`). Mouser leaves NULL. Same encryption envelope. |

Encryption envelope: `app.core.secrets.encrypt` / `decrypt`. Plaintext never appears in a column. Read paths call `decrypt(ws.parts_provider_api_key)` immediately before passing to the provider.

Secondary credentials live in `workspace_provider_credentials` instead — one active row per `(workspace_id, provider)`, with `api_key_encrypted` / `api_secret_encrypted` under the same Fernet keyring. The table starts empty (migration 0070 backfills nothing) and never holds the primary. Written through `PUT /api/workspaces/current/provider-credentials`; exposed only as presence flags in `provider_credentials[]` on `GET /api/workspaces/current`.

## Workspace sourcing settings

TrustedParts sourcing state also lives on the `Workspace` row (`backend/app/domain/workspaces/models.py:48-64`). It is separate from catalog-provider credentials because it drives availability, quoting, and dashboard refresh behaviour rather than one-off MPN enrichment.

| Column | Notes |
|---|---|
| `sourcing_provider` | `none` until a sourcing provider integration is configured. DB default `none`. |
| `sourcing_company_id_enc` | Deprecated encrypted sourcing account/company identifier. Exposed only as `has_sourcing_company_id` on `GET /api/workspaces/current`; not sent to TrustedParts Inventory API v2 requests. |
| `sourcing_api_key_enc` | Encrypted sourcing API key. Exposed only as `has_sourcing_api_key` on `GET /api/workspaces/current`. |
| `sourcing_country_code` | Optional ISO 3166-1 alpha-2 country code for provider locale. |
| `sourcing_currency_code` | Optional ISO 4217 currency code for sourcing responses. |
| `sourcing_preferred_distributors` | Optional JSONB preference payload for distributor ordering/filtering. |
| `sourcing_use_cached_for_dashboards` | Dashboard refreshes may use cached sourcing data. DB default `true`. |

The workspace serializer (`backend/app/api/routes/workspaces.py:118-136`) returns only non-secret sourcing fields and boolean masks. It never serializes plaintext or ciphertext for the encrypted sourcing credential columns.

## Provider factory

`backend/app/domain/parts/providers/base.py::make_provider(name, api_key, api_secret=None)` (`backend/app/domain/parts/providers/base.py:44-65`):

- Returns `None` if `name in (None, "none")` or the required credentials are missing (DigiKey needs both; Mouser only the key).
- Returns a `MouserProvider(api_key=...)` or `DigiKeyProvider(client_id=..., client_secret=...)` instance.

Provider instances are short-lived — built per-request inside the route handler from decrypted credentials. The DigiKey OAuth token cache is per-instance, so it mostly serves within-call reuse rather than cross-request sharing (`backend/app/domain/parts/providers/digikey.py:114-118`).

## Provider protocol

`PartsProvider` (`backend/app/domain/parts/providers/base.py:38-41`):

```python
class PartsProvider(Protocol):
    name: str
    def lookup_mpn(self, mpn: str) -> MpnLookupResult: ...
```

`MpnLookupResult` is `{found: bool, result: dict | None, message: str | None}`.

The canonical `result` shape (`backend/app/domain/parts/providers/base.py:19-34`):

```
{
  "mpn":            str,
  "manufacturer":   str | None,
  "description":    str | None,
  "category":       str | None,
  "footprint":      str | None,
  "datasheet_url":  str | None,
  "image_url":      str | None,
  "source_url":     str,
  "specs":          [{ "key": str, "value": str }, ...],
}
```

`specs` is an ordered list; the names come straight from the upstream provider (Mouser's `ProductAttributes`, DigiKey's `Parameters[]`). The frontend persists each row as a `custom_fields(source='provider')` entry on the new part.

## Mouser

`backend/app/domain/parts/providers/mouser.py`. Single endpoint:

- `POST https://api.mouser.com/api/v1/search/partnumber` with the API key as the `apiKey` **query parameter**, not a header (`backend/app/domain/parts/providers/mouser.py:131`).
- 8s wall-clock timeout (BE2-011) (`backend/app/domain/parts/providers/mouser.py:12`).

Mouser populates `ProductAttributes` only with packaging info; the parametric values live in the free-text `Description`. The provider runs unit-aware regex inference on description tokens to recover specs (`backend/app/domain/parts/providers/mouser.py:23-40`). Tokens that don't match a known pattern are skipped — better to omit a row than write garbage.

## DigiKey

`backend/app/domain/parts/providers/digikey.py`. Two endpoints, OAuth 2-legged:

1. `POST /v1/oauth2/token` — `grant_type=client_credentials` mints a short-lived bearer (~10 min). `_post_token` (`backend/app/domain/parts/providers/digikey.py:44-60`).
2. `GET /products/v4/search/{mpn}/productdetails` — returns the rich product record. `_get_product_details` (`backend/app/domain/parts/providers/digikey.py:74-86`).
3. `POST /products/v4/search/keyword` — fallback fuzzy search when the exact endpoint 404s. Distributor-printed MPNs often differ from DigiKey's canonical indexing (Molex `98266-0897` vs DigiKey `0982660897`) and the keyword search typically finds the right product as the top hit. `_post_keyword_search` (`backend/app/domain/parts/providers/digikey.py:89-105`).

Locale is fixed to `CZ / en / CZK` (`backend/app/domain/parts/providers/digikey.py:30-32`) to match the user's environment. Headers always include `Authorization: Bearer`, `X-DIGIKEY-Client-Id`, locale triplet, and `Accept: application/json` (`backend/app/domain/parts/providers/digikey.py:63-71`).

### OAuth token cache

Per-`DigiKeyProvider` instance (`backend/app/domain/parts/providers/digikey.py:120-131`):

- `_get_token()` returns the cached token if `>60s` of TTL remain, else mints a fresh one.
- `expires_in` from the response feeds `_token_exp = monotonic() + ttl`.

### 401 retry

`_request_with_retry` (`backend/app/domain/parts/providers/digikey.py:133-149`): on 401 (token expired between mint and use, or revoked server-side), invalidate the cache, mint a fresh token, and retry once. The 60s buffer in `_get_token` covers the common case; this guards rare interleavings where DigiKey rotates the token mid-call.

### Result transformation

`_record_from_product` (`backend/app/domain/parts/providers/digikey.py:239-417`) maps a DigiKey `Product` object into the canonical `result` shape. Highlights:

- Walks `Parameters[]` to populate `specs[]`. The first parameter whose name matches `_FOOTPRINT_KEYS` (`{"package / case", "supplier device package", "package", "footprint"}`) becomes the canonical `footprint`.
- Lifts lifecycle / RoHS / REACH / MSL / HTS / ECCN classifications into `specs[]` so the catalog tab shows them.
- Pricing: walks `ProductVariations[].StandardPricing[]`, picks the variation whose lowest `BreakQuantity` is smallest (typically cut-tape, MOQ=1), tie-breaks on cheapest `UnitPrice`, then emits one `Unit price (N+)` spec per tier.
- Packaging names and DigiKey P/Ns collapse to `"Packaging" → "Cut Tape (CT) / Tape & Reel (TR)"` and `"DigiKey P/N" → "311-1.0KGRCT-ND / 311-1.0KGRTR-ND"`.

## Catalog vs spec key split

The hard invariant from `CLAUDE.md`. Both kinds of `specs[]` rows land as `custom_fields(source='provider')` — the split is decided at render time, by key name.

The truth is on the **frontend** in `web/src/lib/providerCatalog.ts:17-52`:

- `CATALOG_LITERAL_KEYS`: `In stock (qty)`, `Lead time`, `Lifecycle`, `End of life`, `Discontinued`, `Marketplace`, `Backorder allowed`, `RoHS`, `REACH`, `HTS code`, `ECCN`, `Packaging`, `Mouser P/N`, `DigiKey P/N`, `Series`.
- `CATALOG_REGEX_KEYS`: `/^Unit price \(\d+\+\)$/`.
- `RESERVED_KEYS`: `image_url`, `datasheet_url` — these don't render in either tab; the layout header surfaces them separately.
- `isSpecKey(key) = !isReservedKey(key) && !isCatalogKey(key)`.

The PartSpecs and PartSourcing tabs split on this boundary. The classification is at render time so adding a new catalog field doesn't need a DB migration to re-categorise historical rows.

The server-side mirror is `backend/app/domain/parts/provider_fields.py`: `PROVIDER_RESERVED_CUSTOM_FIELD_KEYS` (`image_url`, `datasheet_url`, `source_url`) and `PROVIDER_ASSET_CUSTOM_FIELD_KINDS`, consumed by `api/routes/custom_fields.py:15`, `api/routes/parts_refresh.py:34` and `app/mcp/tools/_shared.py:51`. The provider-side field shapes are separate, in `providers/base.py`. Adding a catalog field needs the frontend list **and** the relevant server-side touchpoint.

## Cache + circuit breaker

`backend/app/domain/parts/services/provider_cache.py`. Sits between callers and the provider.

| Concern | Behaviour | Source |
|---|---|---|
| Cache key | `(provider.name, mpn.strip().lower())` — workspaces sharing a provider share cache hits (the result is public catalog data, not workspace-scoped). | `:87-88,15-17` |
| Cache TTL — hits | 24 hours. | `:52` |
| Cache TTL — clean misses (`"no match for MPN"` / `"empty MPN"`) | 5 minutes. | `:56,213-215` |
| Cache size cap | 512 entries, LRU evict. | `:59,109-111` |
| Circuit breaker | 5 consecutive hard failures → open for 60s. Successful call resets counter. | `:62-63,122-145` |
| Hard-failure classification | message contains `"unavailable"`, `"auth failed"`, `"rate limit"`, `"http 5"`, `"http 4"`. `"no match for MPN"` is a clean miss, not a failure. | `:198-205` |
| When breaker open | Returns synthetic `{"found": False, "result": None, "message": "provider temporarily unavailable (circuit breaker open)"}` without hitting upstream. | `:181-188` |

Module-level singleton `_cache` and per-process `_breakers` dict (`backend/app/domain/parts/services/provider_cache.py:114-115,148`). Per-process state is fine because production runs `--workers 1`.

| Operation | Entry point | Notes |
|---|---|---|
| Cached lookup | `provider_cache.lookup_with_cache(provider, mpn)` | Default for scan-import and one-off lookups. |
| Force-fresh | `provider_cache.lookup_fresh(provider, mpn)` | Operator-initiated refresh; skips cache read; still applies breaker. |

## Asset download

`backend/app/domain/parts/services/assets.py::fetch_provider_asset` downloads provider images and datasheets into `UPLOAD_DIR` and returns a sha256-named file. Highlights from the module docstring (`backend/app/domain/parts/services/assets.py:9-27`):

- Host allow-list + DNS-resolves-to-public-IP check. Blocks SSRF into RFC1918 / loopback / link-local / metadata ranges.
- `follow_redirects=False`. A 30x is treated as a refusal so a future CDN swap can't broaden the egress surface.
- No SVG (`image/svg+xml` is not in the MIME map). XML SVG can carry executable payloads.
- Magic-byte validation (SEC2-012) — sniffed file type must match the Content-Type-derived extension.

The content-addressed asset URL contract (`{UPLOAD_DIR}/parts/{ws_id}/{sha}.{ext}` served via `GET /api/parts/assets/{ws_id}/{filename}`) is the [`CLAUDE.md` content-addressed-assets invariant](../../CLAUDE.md) and ADR [`ADR-0005`](../adr/0005-content-addressed-assets.md). Don't change the URL structure — `PartInfo` builds it directly.

## Things to never do

- **Never set `verify=False` on an httpx client.** CI greps for `verify=False`, `trust_env=False`, `ssl=False` under `backend/app/`. Annotate with `# noqa: tls-verify` only for intentional internal test doubles (`CLAUDE.md` — "Hard invariants").
- **Never change `_HIT_TTL_SEC` to be workspace-aware.** Two workspaces share cache hits because the upstream payload is public catalog data; making the key workspace-scoped would burn quota for no gain.
- **Never bypass `lookup_with_cache` in scan-import flows.** Bulk-import does N MPN lookups under a wall-clock deadline; without the cache + breaker, a flaky upstream takes the whole batch down.
- **Never log decrypted credentials.** The audit-log invariant explicitly forbids passing key material through `comment` (`backend/app/domain/audit/service.py:38-40`).
- **Never reconcile provider custom fields without a namespace scope.** The delete pass drops every `source='provider'` row absent from the payload; unscoped, one provider's refresh wipes every other provider's rows. `provider_owns_custom_field_key` is the only place that boundary is expressed (ADR-0031).
- **Never let a secondary provider write a part column.** `manufacturer` / `mpn` / `footprint` / `description` and `parts.linked_*` belong to the primary. A second claimant on them is exactly the ambiguity the namespace model exists to avoid.
