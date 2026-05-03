# Providers

Audience: engineer

Mouser and DigiKey are the two shipped parts-data providers. This page covers the per-workspace configuration model, the OAuth-2leg flow on DigiKey, the catalog vs spec key split, and the cache + circuit-breaker layer.

For the catalog/spec split decision see [ADR-0007](../adr/0007-provider-catalog-vs-spec-split.md).

## Per-workspace credentials

Provider state lives on the `Workspace` row (`backend/app/domain/workspaces/models.py:41-47`):

| Column | Notes |
|---|---|
| `parts_provider` | `none | mouser | digikey`. Default `none`. |
| `parts_provider_api_key` | Mouser API key, or DigiKey `client_id`. Encrypted at rest via `app.core.secrets` (Sec HIGH-9). Fernet ciphertext is ~30% larger after base64; column widened to `varchar(1024)` in alembic 0016 (`backend/app/domain/workspaces/models.py:42-45`). |
| `parts_provider_api_secret` | Only DigiKey uses it (`client_secret`). Mouser leaves NULL. Same encryption envelope. |

Encryption envelope: `app.core.secrets.encrypt` / `decrypt`. Plaintext never appears in a column. Read paths call `decrypt(ws.parts_provider_api_key)` immediately before passing to the provider.

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

- `POST https://api.mouser.com/api/v1/search/partnumber` with the API key as a query param (TODO(verify): exact auth shape — confirm whether key is in URL or header).
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

`CLAUDE.md` invariant: "the same key list lives server-side in `backend/app/domain/parts/services/provider.py`". TODO(verify): that file path doesn't exist in the current tree — only `services/{assets,bag_signature,provider_cache}.py`. Confirm where the server-side mirror landed (or whether it's now read by the catalog endpoint from a constants module).

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
