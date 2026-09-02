# KiCad API

Audience: engineer

`/kicad-api` — the two surfaces KiCad itself talks to: the HTTP library it reads parts from, and the Plugin & Content Manager repository it installs library files from. Neither speaks the app envelope, and both live outside `/api` for that reason.

## Conventions

These pages are the exception to [API conventions](./README.md). What differs, and why:

- **No envelope.** KiCad parses fixed JSON documents; `{ data, status }` wrapped around them would be unparseable (`backend/app/api/routes/kicad.py:6-10`).
- **Every scalar is a string** on `/v1`, including booleans (`"True"` / `"False"`) and ids. `tests/test_kicad_api.py::test_no_non_string_scalars_anywhere` walks the documents rather than checking a field list, so a new field cannot slip an int through.
- **One 404 for everything.** Bad token, unknown category, ineligible part, malformed UUID — all `404` `kicad.not_found`, so nothing on the surface is an oracle (`backend/app/core/errors.py:293-296`). The error *body* is still the app envelope, produced by the global handler.
- **`GET` only.** There is no write surface here.

Both routers mount under one prefix, `API_PREFIX = "/kicad-api"` (`backend/app/domain/eda/kicad_library.py:99`, mounted `backend/app/main.py:643` and `main.py:649`). The constant is shared with `GET /api/eda/kicad-setup` so the advertised `root_url` and the path actually answered cannot drift.

The one deliberate exception to the flattened 404 is **429**: it is raised by the limiter before any router code runs, needs no valid credential to reach, and flattening it would cost the caller its `Retry-After` header.

## Authentication

Two postures, because the two clients differ in what they can send.

| Surface | Credential | Accepted tokens |
|---|---|---|
| `/kicad-api/v1/*` | `Authorization: Token <plaintext>` (`Bearer` also parses) | Any live token. Every route is a `GET`, so `read_only` passes. |
| `/kicad-api/pcm/{token}/*` | URL **path segment** | `read_only` **only**. |

The PCM issues plain GETs with no `Authorization` header — not for the repository, not for `packages.json`, not for the archive (`backend/app/api/routes/kicad_pcm.py:10-18`). So the token rides the path, and a full-parity token in a URL is precisely the leak the `read_only` flag exists for ([ADR-0029](../adr/0029-api-tokens-and-csrf-exemption.md)). One presented there is refused with the same `404` as a revoked one:

```python
# backend/app/api/routes/kicad_pcm.py:118-120
row = request.state.api_token
if not row.read_only:
    _not_found()
```

That check sits **after** authentication on purpose, so the attempt lands in `last_used_at` before it is refused — someone probing this surface with a stolen full-parity token is exactly what that column exists to make visible (`kicad_pcm.py:110-113`).

Session cookies never authenticate either surface. `kicad_workspace` is called as the first line of each route body rather than as a dependency, so the rate limiter sees an invalid token before the 404 does — a dependency would make a credential-stuffing flood free (`kicad.py:102-108`).

**Token in a path is masked in logs.** `core/responses.py::mask_credential_segment` (`backend/app/core/responses.py:106-138`) rewrites `/kicad-api/pcm/<token>/…` before the error log sees it, and `main.py::_scrub_url` reuses the same helper for Sentry. The uvicorn and nginx access logs still record the full URL — inherent to putting a token in a path, and the reason this surface accepts read-only tokens alone (`responses.py:118-121`).

## Rate limits

Two buckets are checked on **every** request, valid credential or not, so rotating the token to dodge the token bucket does not dodge the IP one (`kicad.py:60-66`). Bucket keys are SHA-256 hashes, never slices of the credential (`kicad.py:81-96`, `kicad_pcm.py:89-100`). Live only in prod ([ADR-0012](../adr/0012-uvicorn-single-worker-slowapi.md)).

| Routes | Per token | Per IP |
|---|---|---|
| `/kicad-api/v1/*` | `120/minute` | `240/minute` |
| PCM JSON documents | `60/minute` | `120/minute` |
| `package.zip` | `10/minute` | `30/minute` |

## The HTTP library

KiCad's `kicad_httplib` protocol (KiCad 8/9/10). The client is configured by a `.kicad_httplib` file — see [tokens § Using a token with KiCad](tokens.md#using-a-token-with-kicad) for the format and [eda § client configuration](eda.md#client-configuration) for the endpoint that generates it.

Client-side cache lifetimes are advertised in that file, not enforced here: `PARTS_TTL_SECONDS = 60`, `CATEGORIES_TTL_SECONDS = 600` (`kicad_library.py:107-108`). KiCad's own defaults are 30 s and 600 s; parts is lifted to 60 s and both are written explicitly, because the numbers in the file are what a user reads when asking why an edit has not shown up.

No `Cache-Control`, `ETag` or `Last-Modified` is set on any `/v1` route. The only response header is `X-Content-Type-Options: nosniff` (`kicad.py:73`).

### `GET /kicad-api/v1/`

The protocol handshake. The client only checks that the keys exist.

```json
{ "categories": "", "parts": "" }
```

### `GET /kicad-api/v1/categories.json`

Active categories, ordered by `(sort_order, name)`.

```json
[{ "id": "…uuid…", "name": "Resistors", "description": "" }]
```

A synthetic bucket is appended last when the workspace has parts with no category:

| Key | Value |
|---|---|
| `id` | `"uncategorized"` — not a UUID, so it can never collide with a real category |
| `name` | `"Uncategorized"` |
| `description` | `"Parts without a category"` |

**Notes** — source `backend/app/api/routes/kicad.py:153-158`, document `kicad_library.py:369-401`.

### `GET /kicad-api/v1/parts/category/{category_id}.json`

Every eligible part in a category, ordered by `(name, id)`. `category_id` is a category UUID or the literal `uncategorized`.

### `GET /kicad-api/v1/parts/{part_id}.json`

One part. **The same document shape** the category listing emits — there is no second copy of it to drift (`kicad_library.py:12-22`).

A part is eligible only if it is active **and** its `symbolIdStr` resolves. Resolution order for both symbol and footprint: the external ref on `part_eda`, then the hosted row, then the category default, then nothing (`kicad_library.py:255-273`). A part that loses its symbol vanishes from the listing and its detail becomes a 404 — deliberate, because a chooser entry that cannot be placed is worse than a missing one.

**Top level**

| Key | Wire type | Source |
|---|---|---|
| `id` | string | `str(part.id)` |
| `name` | string | `part.name` |
| `symbolIdStr` | string | `PCM_SM_<slug>:<entry>` |
| `description` | string | `part.description` or `""` |
| `keywords` | string | `part_eda.keywords` or `""` |
| `exclude_from_bom` | **string** `"True"` / `"False"` | `part_eda`, default `"False"` |
| `exclude_from_board` | **string** | `part_eda`, default `"False"` |
| `exclude_from_sim` | **string** | `part_eda`, default **`"True"`** — no config means no simulation model, and KiCad treats a symbol claiming simulability it lacks as an error |
| `fields` | object of objects | see below |
| `footprint_filters` | array of strings | **key omitted entirely** when empty |

**`fields`** — each entry is `{"value": "…", "visible": "False"}`, in this insertion order. An empty value is skipped rather than emitted: an empty KiCad field is not nothing, it is a property drawn on every instance of the symbol with no content in it (`kicad_library.py:345-352`).

| Key | Present when |
|---|---|
| `footprint` | the footprint ref resolves |
| `datasheet` | a datasheet URL resolved |
| `value` | **always** — and carries no `visible` key, so the symbol's own default wins |
| `description`, `keywords`, `MPN`, `Manufacturer`, `IPN` | the underlying value is non-empty |
| `StockManager` | **always** — `<APP_BASE_URL>/parts/<part_id>` |
| `Sim.Device`, `Sim.Pins`, `Sim.Params`, `Sim.Library` | the part has a non-archived SPICE data file **and** `exclude_from_sim` is false |

`Sim.Library` is `${STOCKMGR_SPICE}/<name>` — the one reference the PCM package cannot fix up for itself, because it is served as JSON here rather than stored in bytes the packager rewrites. The user sets that path variable by hand; `GET /api/eda/kicad-setup` supplies the value.

The datasheet URL comes from the `datasheet_url` custom field. A value starting `/` is made absolute against `APP_BASE_URL`; anything not `http://` or `https://` is **dropped**, because `file:`, `javascript:` or a bare Windows path is a request to open something local on the engineer's machine, sourced from provider data we do not control (`kicad_library.py:126-131`).

**Notes** — source `kicad.py:161-205`, document `kicad_library.py:439-492`.

## The PCM repository

The HTTP library only *names* symbols and footprints. This repository ships the files, as one add-on package per workspace.

All three documents set `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, `X-Robots-Tag: noindex, nofollow` and `X-Content-Type-Options: nosniff` (`kicad_pcm.py:68-73`). `no-store` because the URL is a credential and these bodies name it in turn; `noindex` because a repository URL pasted into a wiki should not become a crawlable one. The PCM does its own version comparison and never relies on HTTP caching.

### `GET /kicad-api/pcm/{token}/repository.json`

```json
{ "name": "<workspace> (stockManager)",
  "maintainer": { "name": "stockManager", "contact": {} },
  "packages": { "url": "…/packages.json", "sha256": "…",
                "update_timestamp": 1767225600, "update_time_utc": "2026-01-01 00:00:00" } }
```

`update_timestamp` is the only integer at this level, and is what the PCM compares to decide whether to re-fetch `packages.json`. It tracks the newest content change rather than the wall clock, so an unchanged workspace answers identically every time.

`sha256` is computed over the exact `packages.json` bytes — the route builds that document a second time and hashes the result, which is why `pcm.json_bytes` serialises with fixed separators rather than letting `JSONResponse` re-encode (`backend/app/domain/eda/pcm.py:1104-1114`).

### `GET /kicad-api/pcm/{token}/packages.json`

`{"packages": []}` for an empty workspace. Otherwise one package:

```json
{ "packages": [{ "name": "…", "identifier": "com.stockmanager.<32 hex>", "type": "library",
                 "license": "unrestricted", "author": { "name": "stockManager", "contact": {} },
                 "versions": [{ "version": "1.244.15300", "status": "stable", "kicad_version": "8.0",
                                "download_url": "…/package.zip", "download_sha256": "…",
                                "download_size": 51234, "install_size": 98765 }] }] }
```

`download_size` and `install_size` are integers; everything else is a string. The four download keys appear **only** here — the PCM schema is explicit that they must not be inside the archive's own `metadata.json`, since they describe the archive.

`license` is `"unrestricted"` and that is load-bearing. The v1 schema closes the field to a 90-value enum and `PLUGIN_CONTENT_MANAGER::ValidateJson` rejects the **whole document** over one bad value — `"proprietary"`, which reads as the obvious label and passes v2, silently stopped the repository from loading at all. `unrestricted` is the enum's catch-all for "no standard licence applies", and `tests/test_kicad_pcm.py` validates the served bytes against the vendored schema so it cannot regress (`pcm.py:147-157`).

`kicad_version` is `"8.0"` — the **floor**, not the target: the oldest release whose PCM understands everything emitted, and the major that `THIRD_PARTY_VAR` is pinned to.

### `GET /kicad-api/pcm/{token}/package.zip`

The archive. Served even for an empty workspace (holding only `metadata.json`), because a 404 there would be a state oracle telling a caller whether the workspace had any content.

Member layout:

| Path | Contents |
|---|---|
| `metadata.json` | the package document, minus the four download keys |
| `symbols/SM_<slug>.kicad_sym` | one library file per category |
| `footprints/SM_<slug>.pretty/<name>.kicad_mod` | `.pretty` is required by KiCad's traverser |
| `3dmodels/<name>` | flat; `.3dshapes` is not required |
| `resources/spice/<name>` | SPICE decks |

Archives are **byte-deterministic**: fixed member order, fixed zip timestamps, fixed create-system — so `download_sha256` is stable for identical content (`pcm.py:53-58`).

Build capacity is bounded by a two-slot semaphore with a 30 s wait. Exhaustion, a stored file gone missing, an unparseable stored footprint, or content over 200 MiB answer **503** `kicad.package_unavailable` — the one status other than 404 and 429 this surface emits. Reaching it needs a valid read-only token, so unlike the 404 it is not an oracle (`pcm.py:250-261`).

## The naming contract

`backend/app/domain/eda/kicad_refs.py` is the contract module: phase 5 *serves* these strings, phase 6 *generates* the files they name, and if the two disagree by a single character KiCad reports a broken symbol on every part in the workspace. Both sides import from there rather than formatting their own strings.

| Piece | Value | Whose is it |
|---|---|---|
| `SM_` | `LIBRARY_PREFIX` (`kicad_refs.py:142`) | ours — namespaces generated libraries away from stock KiCad ones |
| `PCM_` | `PCM_NICKNAME_PREFIX` (`kicad_refs.py:147`) | **KiCad's** — prepended by the PCM when it registers an installed package's libraries. We cannot opt out, only predict it |
| `<slug>` | `part_categories.library_slug` | derived from the category name; `uncategorized` when there is none |

So a reference is `PCM_SM_<slug>:<entry>`, e.g. `PCM_SM_resistors:R_10k`.

**The slug comes from the entry's own category, not the referencing part's** (`kicad_refs.py:40-43`). A symbol filed under *Resistors* used by a part filed under *Passives* is `PCM_SM_resistors:…`.

An entry whose category was **archived** is treated as having none, so it is referenced and packaged as `PCM_SM_uncategorized`. A real category could in principle slugify to `uncategorized` too; that collision merges the two libraries rather than breaking either, which is why it is tolerated rather than guarded.

Other contract values:

| Function | Result |
|---|---|
| `package_identifier(ws_id)` | `com.stockmanager.<32 hex>` — one package per workspace |
| `install_dir_name(id)` | `com_stockmanager_<hex>` — the PCM replaces dots with underscores |
| `pcm_model_path(id, name)` | `${KICAD8_3RD_PARTY}/3dmodels/com_stockmanager_<hex>/<name>` |
| `pcm_spice_dir(id)` | `${KICAD8_3RD_PARTY}/resources/com_stockmanager_<hex>/spice` |

`THIRD_PARTY_VAR` is **versioned on purpose**: `KICAD8_3RD_PARTY` on 8, `KICAD9_3RD_PARTY` on 9, with no unversioned spelling and a hand-created `KICAD_3RD_PARTY` not recognised. Hard-coding one major is safe because KiCad re-points an unresolvable `${KICADn_3RD_PARTY}` itself; 8 is pinned to match the `kicad_version` floor (`kicad_refs.py:86-98`).

`${STOCKMGR_3D}` is what stored footprints carry, and the packager rewrites it to `pcm_model_path` at build time so an installed package resolves 3D models with zero configuration. `${STOCKMGR_SPICE}` cannot get the same treatment — it is served as JSON, not packaged — so it is the one variable the user sets by hand.

## Package versions

The version is `<major>.<minor>.<patch>`, derived from the newest `updated_at` across symbols, footprints, data files and categories — archived rows included (`pcm.py:359-422`):

- major = `1 + days_since_epoch / 10000`
- minor = `days_since_epoch % 10000`
- patch = half-seconds into the day

`VERSION_EPOCH` is `2026-01-01T00:00:00Z`, and anything older is clamped up, because a negative component is rejected outright by the PCM's version pattern.

The **cache key is a separate fingerprint**, not the version: a SHA-256 over the identifier, version, workspace name and every entry's stem, name and hash. Two edits inside one two-second tick share a version but not a fingerprint, and keying the cache on the version would serve the first edit's zip forever (`pcm.py:613-642`).

**Known gap, documented in code**: renaming the workspace moves neither the version nor `update_timestamp`, because `workspaces` has no `updated_at`. The served documents and the archive still agree — the workspace name is in the fingerprint — but KiCad will not notice the new name until the next real content change (`pcm.py:395-414`).

`eda_footprint_models` carries no timestamps, so linking a 3D model bumps the **footprint's** `updated_at` explicitly. Without that, attaching a model would alter the package's contents without advancing its version, and no installed copy would ever be offered the update (`backend/app/domain/eda/service.py:49-65`).

## See also

- [eda](eda.md) — the `/api/eda` surface that fills the library
- [tokens](tokens.md) — minting the credential, and the `.kicad_httplib` file format
- [domain/eda](../domain/eda.md) — the tables and the packager internals
- [user/kicad](../user/kicad.md) — the same setup, for an operator
- [ADR-0029](../adr/0029-api-tokens-and-csrf-exemption.md) — token design and the read-only flag
