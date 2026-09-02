# EDA libraries

Audience: engineer

The five tables behind a workspace's KiCad library, the storage lane they use, and the four modules that parse, import, package and name their contents. For the HTTP surface see [api/eda](../api/eda.md); for what KiCad reads, [api/kicad](../api/kicad.md).

## Tables

Created by `backend/alembic/versions/0068_eda_domain.py` — the only migration that touches them. Models at `backend/app/domain/eda/models.py`, registered in `backend/app/domain/all_models.py:32-38`.

| Model | Table | Source |
|---|---|---|
| `EdaSymbol` | `eda_symbols` | `backend/app/domain/eda/models.py:32` |
| `EdaFootprint` | `eda_footprints` | `backend/app/domain/eda/models.py:71` |
| `EdaDatafile` | `eda_datafiles` | `backend/app/domain/eda/models.py:103` |
| `EdaFootprintModel` | `eda_footprint_models` | `backend/app/domain/eda/models.py:131` |
| `PartEda` | `part_eda` | `backend/app/domain/eda/models.py:170` |

All but `eda_footprint_models` are `WorkspaceOwned` (`backend/app/domain/_mixins.py:11-20`), which supplies `id`, `workspace_id`, `created_at`, `updated_at`, `created_by`, `updated_by`, `archived_at`.

### The three library tables

`eda_symbols` and `eda_footprints` are the same column set: `name` `String(200)`, `sha256` `String(64)`, `size_bytes`, `source` `String(20)` default `'manual'`, and `category_id` → `part_categories.id` `SET NULL`.

`eda_datafiles` swaps `category_id` for `kind` `String(10)` — data files are not filed into categories. `kind` is one of `("step", "wrl", "spice")` (`models.py:29`) with **no DB CHECK and no enum**; it is policed in code only.

`source` is likewise a bare string, from `EDA_SOURCES = ("manual", "snapeda", "samacsys", "ultralibrarian", "easyeda")` (`models.py:25`). A client never sets it.

Uniqueness is **partial on active rows**, which is what makes archiving free a name:

| Index | Predicate |
|---|---|
| `uq_eda_symbols_ws_name` on `(workspace_id, name)` | `archived_at IS NULL` |
| `uq_eda_footprints_ws_name` on `(workspace_id, name)` | `archived_at IS NULL` |
| `uq_eda_datafiles_ws_kind_name` on `(workspace_id, kind, name)` | `archived_at IS NULL` |

### `eda_footprint_models`

The footprint ↔ 3D-model join. Plain `Base`, deliberately **not** `WorkspaceOwned`: a join row has no independent lifecycle, so `archived_at` and `created_by` would be dead columns. Same shape as `parts.models.PartCadKey`.

Columns: `id`, `workspace_id` → `workspaces.id` CASCADE, `footprint_id` → `eda_footprints.id` CASCADE, `datafile_id` → `eda_datafiles.id` CASCADE, `position` default `0`. Unique on `(footprint_id, datafile_id)` — **not** workspace-qualified, since both parents already are.

`workspace_id` is carried redundantly (derivable through either parent) so a join row can be listed without a join, and every query filters on it (`models.py:136-139`).

It carries **no timestamps**, which is why `service.py::_touch` writes the footprint's `updated_at` by hand on link and unlink — see [package versions](#why-a-3d-link-touches-the-footprint).

### `part_eda`

1:1 with `parts`, enforced by `uq_part_eda_part` (a plain unique, not partial).

Each of the symbol and footprint slots is a **pair**, and only one half may be set:

- `symbol_id` / `footprint_id` → a definition this workspace hosts.
- `symbol_ref_external` / `footprint_ref_external` → a KiCad `LibNick:Entry` string (e.g. `"Device:R"`) naming something in the user's own installed libraries. We store the reference and never the file.
- Both null → inherit the category default.

The XOR is enforced **three ways** (`models.py:183-185`): a 422 `eda.ref_conflict` in the service, a CHECK constraint (`ck_part_eda_symbol_ref_exclusive`, `ck_part_eda_footprint_ref_exclusive`), and the same CHECKs in migration 0068.

The rest is KiCad symbol metadata: `value`, `keywords`, `footprint_filters` (`ARRAY(String(100))`), the three exclusion booleans, and `sim_device` / `sim_pins` / `sim_params` — free-form `Sim.*` strings KiCad parses and we only carry.

`exclude_from_sim` defaults **true** while the other two default false: most parts have no simulation model, and KiCad errors on a part that claims simulability it does not have (`models.py:245-247`).

**Delete behaviour**

| Event | Effect |
|---|---|
| Part hard-deleted | The `part_eda` row goes with it — `parts.id` CASCADE. |
| Symbol / footprint / data file hard-deleted | The reference is nulled, the configuration survives — all three library FKs are `SET NULL`. |
| Workspace deleted | CASCADE via the mixin. |

`archived_at` exists on `part_eda` from the mixin but is **never set**. The configuration is deleted outright, because a soft-archived one would still collide on `uq_part_eda_part` on the way back and there is no tombstone worth keeping (`models.py:187-190`).

Migration 0068 adds **no BEFORE triggers**, unlike `parts.category_id` in 0067, because nothing writes these five tables outside the service. See [workspace-isolation](workspace-isolation.md).

## Storage lanes

`backend/app/domain/eda/storage.py`. On disk: `{UPLOAD_DIR}/eda/{ws_id}/{sha256}.{ext}` — content-addressed, per [ADR-0005](../adr/0005-content-addressed-assets.md).

**Why this is a separate lane from attachments.** The attachment and provider-asset stores validate by **magic bytes against an allow-list of binary formats**, and that allow-list must not be loosened, because every format it admits is one a browser will render inline. KiCad libraries are text, have no magic number, and are never served inline — so they get their own structural validators here rather than a hole punched in those (`storage.py:1-10`).

Two sub-lanes inside it:

| Lane | Kinds | What happens |
|---|---|---|
| Text-CAD | `symbol`, `footprint` | Decoded as UTF-8, NUL-checked, parsed as an s-expression, root token checked. **The stored bytes are the re-emitted canonical form**, not what was uploaded — so anything that survives is by construction re-parseable. |
| Verbatim | `step`, `wrl`, `spice` | Leading signature checked (`ISO-10303-21`, `#VRML`), or for SPICE nothing beyond UTF-8 — the simulator is the arbiter. Stored exactly as received, because we never parse them. |

`EXT_BY_KIND` (`storage.py:74-80`) normalises stored extensions: `kicad_sym`, `kicad_mod`, `step`, `wrl`, and `lib` for every SPICE spelling, because they all mean the same thing to the simulator.

**Caps are checked twice.** Once before any parsing, because parsing is where the CPU goes; and again on the **re-emitted** form, because regenerated indentation amplified a deep-and-wide file ~198x past the input cap (`storage.py:161-181`). The same re-check caps the parsed entry name at 200 to match the column, since a longer one would die in Postgres as a `DataError` 500 rather than a 422.

**Digest first, store last.** Routes and the importer compute the hash, insert the row, and only then write the blob — so a rejected upload (409 name conflict, 404 category) leaves no orphan file (`storage.py:292-293`). Writes are `mkstemp` + `os.replace`, skipped entirely when the target already exists. The scratch name must be unique **per writer, not per target**: a shared `{sha}.tmp` meant the first `os.replace` moved the file out from under a concurrent identical import, which died with `FileNotFoundError`.

`decode_text` is public precisely so the zip importer applies the identical guard. A lone NUL is valid UTF-8, and a second laxer decoder is how a NUL reaches a `text` column as a 500 instead of a 422.

**Orphan blobs are never swept.** A rename re-emits to a new hash and the old blob stays on disk unreferenced — consistent with the repo-wide no-sweeper stance.

## The s-expression contract

`backend/app/domain/eda/sexpr.py` — a tokenizer and an emitter, not a format binding. It does exactly four things: name an entry, rename it, read/write a property, rewrite model references. Every node it does not understand is left untouched. `kiutils`, the only maintained candidate, lags the format by a release or two and would fail on nodes it has not been taught — precisely the wrong behaviour for a user's upload.

| Entry point | Purpose |
|---|---|
| `parse(text) -> Node` | Parse one top-level node. `SexprError` means "these bytes are not a KiCad library file", and callers turn it into a 422. |
| `emit(node) -> str` | Render back to KiCad-flavoured text. |
| `entries(libtext) -> [(name, node)]` | The symbol entries in a library. |
| `entry_name(node) -> str` | The first string argument of `(symbol "NAME" …)`. |
| `rename(node, new_name) -> Node` | Copy renamed, including prefix-matched unit sub-symbols. |
| `get_property` / `set_property` | Read or write a `(property "key" "value" …)` child. |
| `model_paths(node) -> [str]` | The 3D paths a footprint references — direct children only. |
| `rewrite_model_paths(node, fn) -> Node` | Copy with each path through `fn`; `fn` returning `None` **drops** that `(model …)` node. |

What the contract guarantees:

- **Semantically stable, not byte-stable.** Whitespace and indentation are normalised; token content, quoting and ordering are preserved exactly.
- **`Quoted` is load-bearing.** Without a marker for atoms that were written `"…"`, the bare token `yes` and the string `"yes"` — different things to KiCad — round-trip to the same text.
- **Nothing mutates in place.** `rename`, `set_property` and `rewrite_model_paths` return new nodes sharing untouched sub-nodes.
- **Unrecognised escapes keep both characters.** `emit` has no inverse for a bare kept character, and vendor footprints carry Windows model paths full of non-canonical escapes.
- **`rename` renames unit sub-symbols.** A symbol's graphical units are nested `(symbol "NAME_<unit>_<style>" …)` children; renaming the parent alone leaves a symbol that draws as blank. `(extends "PARENT")` is deliberately untouched — it names a different entry.
- **`set_property` keeps position.** An existing property keeps its slot and its trailing `(at …)` / `(effects …)` nodes, so rewriting a value does not move the field on the schematic. A new one is appended after the last existing property, because KiCad reads the mandatory four positionally.

Two limits that are security properties, not omissions: `parse` is **iterative with an explicit stack**, so a deeply nested upload is a 422 rather than a `RecursionError` 500; and `_MAX_DEPTH = 32` bounds `emit`, which *is* recursive — real KiCad files sit around 6 levels, and combined with the post-emit size cap this closes the indentation-amplification lever.

## The import pipeline

Three files, one split (`backend/app/domain/eda/importer.py:1-6`):

| Module | Owns |
|---|---|
| `vendor_zip.py` | What a zip or bare `.kicad_sym` offers. **Touches no database and no filesystem**, which is what makes detection and the ambiguity rules testable without a request. |
| `lcsc.py` | What an LCSC part offers, via the `easyeda2kicad` package used as a library. |
| `importer.py` | The only thing that writes any of it down. |

Both producers emit the same `ImportPlan` — `vendor`, `symbols`, `footprints`, `datafiles`, `skipped` — so a fix to the storage or wiring rules lands on both paths at once.

### The plan

- `PendingEntry(name, node, filename)` — a parsed symbol or footprint, not yet canonicalised or stored.
- `PendingDatafile(kind, name, data)` — a validated 3D or SPICE model. Its `.stem` is how a footprint's `(model …)` path is matched back.
- `Skipped(filename, reason)` — `filename` clamped to 80 chars, because member names are attacker-supplied and a zip may hold 200 of them.

### The writer

`importer.import_plan(db, *, ws, user_id, plan, source, category_id=None) -> ImportResult`. The caller owns the transaction.

**Order is load-bearing: data files land first**, because a footprint's `(model …)` paths are rewritten to point at the rows they produce, and the row names are not known until the conflict-suffix walk has run.

Three rules carried over from the single-file upload routes (`importer.py:8-22`):

1. **Digest first, store last** — same reason as the upload lane.
2. **Name conflicts suffix, they do not fail.** A single-file upload answers 409 and lets the user rename; an archive cannot, so a colliding name takes a ` (2)`-style suffix up to ` (9)` before surfacing the 409. The suffix goes **before** the extension for data files (`P (2).step`, never `P.step (2)`), because KiCad picks its 3D plugin by extension; symbol and footprint names are KiCad *entry* names, not filenames, so they take it at the end.
3. **A bad member is a note, not an error.** It lands in `skipped` and the rest still imports.

**`render` re-runs for every candidate name.** A symbol carries its name *inside* the file, so a suffix that renamed only the row would leave `MYPART (2)` pointing at bytes still saying `(symbol "MYPART")`.

**Model-path rewriting** points `(model …)` nodes at `${STOCKMGR_3D}/<row name>`, matching by folded stem (lowercase alphanumerics only, because vendors disagree about case and `-` vs `_`). A stem maps to a **list** of rows, since `ABC.step` and `ABC.wrl` are one model in two formats and a path naming either should attach both; STEP wins when both are present, and is sorted first because KiCad renders the first model. If the footprint referenced models but matched **none**, the paths are left exactly as the vendor wrote them — rewriting to nothing is strictly worse than a path the user might resolve locally. Otherwise unmatched paths are dropped and each is named individually in `skipped`, because a *partial* drop is the quiet failure.

`wire_part` fills **empty slots only** unless `overwrite`. A slot naming an external `LibNick:Entry` counts as occupied; taking the hosted side clears the external one, because the CHECK constraint forbids holding both. `value`, `keywords` and the exclusion flags are never touched — no vendor archive knows better. When there is no configuration yet the row is built **detached** and attached only if something actually gets filled, so an import with nothing to wire leaves no empty configuration behind.

### Vendor detection

By directory name, **case-sensitively** — the capital `CAD` is the whole discriminator, and `KiCad` vs `KiCAD` are two vendors one letter apart. Layout detection mirrors `Steffen-W/Import-LIB-KiCad-Plugin`.

The vendor decides only the `source` column; nothing about extraction branches on it. An archive matching none of the shapes is still imported — the cost of guessing wrong is a mislabelled `source`, the cost of refusing is a user who cannot import a perfectly good archive. See [api/eda § importers](../api/eda.md#importers) for the table.

**Legacy `.lib` is rejected, with the fix in the message.** `EESchema-LIBRARY` is the pre-6.0 symbol format, unreadable here because reading it needs `kicad-cli`, which is deliberately not run at request time. A `.lib` member is classified by sniffing: legacy magic → skipped and flagged; positive SPICE evidence (`.subckt`, `.model`, `.include`, … as a line prefix in the first 4 KiB) → a SPICE data file; neither → skipped as ambiguous. Evidence must be **positive**, because a vendor `.lib` is far more often a legacy symbol library than a simulation deck. If the archive carried a legacy library and produced no symbols at all, the whole import is a 422 naming `kicad-cli sym upgrade`.

### Archive bounds

Layered, and every layer is there because something got past the one above (`vendor_zip.py:64-89`):

| Bound | Value | Checked against |
|---|---|---|
| `MAX_MEMBERS` | 200 | the central directory, before anything is decompressed |
| `MAX_UNCOMPRESSED_BYTES` | 50 MiB | the central directory, **and again** against the real inflated total as chunks leave zlib |
| `MAX_PARSED_TEXT_BYTES` | 8 MiB | running total of text actually parsed |
| `MAX_ENTRIES` | 200 | rows one import may create |

`MAX_PARSED_TEXT_BYTES` is the one that bounds memory: a parsed node tree runs ~20x its source text, so 50 MiB of legal `.kicad_sym` would peak near a gigabyte and OOM a 1 GB container from a ~130 KiB upload. Members are read in 64 KiB chunks, so one lying about its size costs a chunk past the cap rather than a full cap of decompression — 200 of those add up. `MAX_ENTRIES` is enforced **inside** the walk, never by trimming a finished list: trimming means everything was parsed and retained first, the exact cost the cap exists to avoid.

`read_archive` is CPU-bound — call it through `run_in_threadpool`.

### Narrowing to one part

`narrow_to_part` collapses a plan to at most one symbol and one footprint. Tiers are tried in order and **the first that resolves wins; they are never unioned**, because unioning lets weak evidence veto strong — a filename hint matching a second symbol would turn an otherwise unambiguous archive into a 422.

Symbol tiers: (1) the symbol whose `Footprint` property names one of this archive's footprints — an explicit link the vendor wrote; (2) the hints (archive filename, part MPN, part IPN); (3) a symbol named after a footprint in the archive. Footprint tiers: (1) the footprint the chosen symbol references; (2) the hints. A tier matches only when **exactly one** entry does.

### LCSC

`easyeda2kicad` is used as a **library, never as a CLI** — a CLI would mean a subprocess, a writable working directory and no way to bound the fetch. Imports are lazy, because the package pulls in its whole converter tree and only this endpoint needs it. `fetch_plan` is the single place tests monkeypatch to keep the network out of the suite.

Stages (fetch → symbol → footprint → 3D) are independent: a component with no 3D model still yields its symbol, and the gaps are `skipped` notes rather than errors. A 20 s budget is re-checked between every stage, under a 30 s outer timeout; the two must not be equal, or the outer fires first and every per-stage check becomes dead code.

**Path safety.** `easyeda2kicad`'s 3D exporter builds its output path straight from EasyEDA's JSON `title`, so an upstream name carrying `../` writes outside the temp directory — a remote arbitrary-file-write. Two mitigations, both applied: the name is forced through a sanitiser on both the importer's model and the exporter's output before anything is written, and the converted file is re-checked with `os.path.realpath` to confirm it is still under the temp directory (which also covers a symlink planted inside it).

## Service

`backend/app/domain/eda/service.py`. Every function is workspace-scoped; writes `db.flush()` and the `get_db` dependency owns the commit.

| Operation | Entry point | Notes |
|---|---|---|
| List one library table | `service.py::list_entries` | Ordered by `(kind, name)` for data files, `name` otherwise. |
| Fetch one entry | `service.py::get_entry` | Archived included, so restore still resolves. 404 re-coded to the model-specific code. |
| Record an upload | `service.py::upload_entry` | Returns `(row, created)`. Dedupes on identical bytes. |
| Rename / re-file | `service.py::update_entry` | Rewrites the stored s-expression when the name moves. |
| Archive / restore | `service.py::archive_entry`, `::restore_entry` | Both idempotent; restore re-checks the freed name. |
| 3D links | `service.py::link_footprint_model`, `::unlink_footprint_model` | Both return the **footprint**. |
| Part configuration | `service.py::get_part_eda`, `::upsert_part_eda`, `::delete_part_eda` | `upsert` is full-replacement; `delete` is a hard delete. |

The three library tables differ only in which columns take part in their uniqueness rule, so list/get/rename/archive/restore are written **once** against a `Model` parameter and described by `_LIBRARY_META`. The per-model behaviour that genuinely differs — what a valid upload looks like, whether a category may be attached — stays at the call site.

Every race on a partial unique index is mapped to the same 409 by inspecting `exc.orig.diag.constraint_name` under a savepoint; without it, the loser of two concurrent uploads gets a 500.

### Workspace isolation

Code-enforced only, per [ADR-0002](../adr/0002-code-enforced-workspace-isolation.md) — there is no DB trigger on any `eda_*` table. Six mechanisms, all in [workspace-isolation](workspace-isolation.md)'s house style:

1. Every read filters on `ws.id`.
2. Every by-id lookup goes through `assert_in_workspace`, so a foreign UUID is a 404, not a cross-tenant read.
3. `get_entry` re-codes **only** the 404 to the model-specific code; a 403 or 400 passes through untouched rather than being silently rewritten.
4. Every write stamps `workspace_id = ws.id`.
5. Cross-table references are resolved through the same guards before use — and additionally kind-checked (a SPICE file cannot be linked as a 3D model, and `spice_datafile_id` cannot name a STEP file).
6. The join table carries and filters on its own `workspace_id`, even though it is derivable through both parents.

The importer validates `category_id` **up front** rather than relying on `upload_entry`: only symbols and footprints carry a category, so a datafile-only archive would never reach that check and a foreign id would come back 200 instead of 404.

## The packager

`backend/app/domain/eda/pcm.py` builds the add-on package; `kicad_refs.py` is the naming contract both it and the HTTP-library endpoint import from. Both are documented where they are consumed — see [api/kicad § the PCM repository](../api/kicad.md#the-pcm-repository) and [§ the naming contract](../api/kicad.md#the-naming-contract).

### Why a 3D link touches the footprint

`eda_footprint_models` carries no timestamps, so the footprint's `updated_at` is the only record that its 3D models changed — and the package derives its version from exactly those timestamps. An ORM object with no *net* attribute change produces no `UPDATE` for `onupdate` to fire on, so without the explicit `_touch`, attaching a model would alter the package's contents without advancing its version, and no installed copy would ever be offered the update.

## See also

- [api/eda](../api/eda.md) — the REST surface
- [api/kicad](../api/kicad.md) — the KiCad-facing protocols
- [data-model](data-model.md) — where these tables sit in the ER diagram
- [parts](parts.md) — the entity `part_eda` hangs off
- `backend/app/domain/eda/README.md` — in-tree orientation
- [phases/14](../phases/14-kicad-and-agent-api.md) — why any of this exists
