# Components

Audience: engineer

The reusable components in `web/src/components/`. Page-specific components
that live under `routes/**` are out of scope — when in doubt, grep.

## DataTable

`web/src/components/DataTable.tsx:135-467`. The default list-rendering
component for every page that shows rows. Use it before rolling your own
table (CLAUDE.md → "Frontend conventions worth preserving").

Current route-level consumers include `/parts` and the project BOM table.
The BOM table uses `rowCanClick` so matched rows navigate to the part detail
page while unmatched rows stay visually muted and inert.

### Feature catalog

| Feature | Source | Test |
|---|---|---|
| Free-text search across all column accessors | `DataTable.tsx:198-209` | `DataTable.dom.test.tsx:149-163` (`initialSearch`) |
| Click-header sort (toggles asc → desc → asc) | `DataTable.tsx:347-373` | `DataTable.dom.test.tsx:52-82` |
| Hidden columns (column-toggle dropdown) | `DataTable.tsx:316-330` | — |
| Persist hidden columns + density to localStorage | `DataTable.tsx:114-126`, key = `dt:${tableId}` | — |
| CSV export with formula-injection hardening | `DataTable.tsx:20-41`, `:258-274` | `DataTable.test.tsx:14-78` |
| Multi-select with select-all-visible header checkbox | `DataTable.tsx:160-167`, `:276-291`, `:411-426` | `DataTable.dom.test.tsx:165-211` |
| Per-row click gating and row class hooks | `DataTable.tsx` `rowCanClick`, `rowClassName` props | `ProjectBOM.test.tsx` matched/unmatched row navigation |
| Density toggle (comfortable / compact) | `DataTable.tsx:230-231`, `:307-315` | — |
| Keyboard navigation (Enter / Space activates row) | `DataTable.tsx:391-403` | `DataTable.keyboard.dom.test.tsx:50-86` |
| Row aria-label built from first textual column | `DataTable.tsx:43-63`, `:395-396` | — |
| Selection pruning across refetch (FE2-007) | `DataTable.tsx:65-74`, `:179-196` | `DataTable.dom.test.tsx:165-211` (FE2-007) |
| Zebra striping + selected highlight | `DataTable.tsx:404-409` | — |
| Empty / filtered footer counts (`X of Y rows`) | `DataTable.tsx:449-454` | — |
| Clear-sort affordance | `DataTable.tsx:455-463` | — |

### CSV export hardening (FE2-008)

Three things `escapeCsvCell` (`DataTable.tsx:22-34`) and `buildCsv`
(`DataTable.tsx:36-41`) get right that a naïve CSV writer doesn't:

1. **Excel formula-injection mitigation (CWE-1236).** Any string cell
   whose first char is `=`, `+`, `-`, `@`, tab, or CR is prefixed with a
   single quote. Numbers are NOT neutralised — a numeric `-5` is
   legitimate, but a string `"-5"` (e.g. typed by a user into a notes
   field) becomes `'-5` (`DataTable.tsx:20`, `:30`). Pinned in
   `DataTable.test.tsx:26-31`.
2. **RFC 4180 quoting.** Embedded `"` doubles to `""`; embedded CR/LF
   round-trips inside the quoted cell. Pinned in `DataTable.test.tsx:46-51`.
3. **UTF-8 BOM up front + CRLF line terminators** so Excel auto-detects
   encoding and Windows text editors don't smush rows together
   (`DataTable.tsx:39-41`). Pinned in `DataTable.test.tsx:55-65`.

`exportCsv` calls `URL.revokeObjectURL` after dispatching the click
(`DataTable.tsx:273`) — without it, long-lived sessions exporting
repeatedly would leak blob URLs.

### Selection lifecycle (FE2-007)

Two effects keep the selection set sane:

```ts
// web/src/components/DataTable.tsx:179-196
useEffect(() => { setSelected(new Set()); }, [tableId]);

useEffect(() => {
  setSelected(prev => {
    const pruned = pruneSelection(prev, allRowIds);
    if (pruned.size === prev.size) return prev;  // avoid render loop
    return pruned;
  });
}, [allRowIds]);
```

- `tableId` change → clear selection (navigating from /parts to /orders
  used to carry stale part ids; bulk-action would happily target rows
  the user couldn't see).
- Row-list change → prune ids that no longer appear (refetch dropped
  some rows).

`pruneSelection` is exported and unit-tested separately in
`DataTable.test.tsx:80-97`.

### Column shape

```ts
// web/src/components/DataTable.tsx:78-86
export type Column<T> = {
  key: string;
  header: string;
  render?: (row: T) => ReactNode;
  accessor?: (row: T) => string | number | boolean | null | undefined;
  width?: string;
  hidden?: boolean;            // initial-hidden default; user can toggle back on
  align?: Align;               // "left" | "right" | "center"
};
```

Default alignment is right for numeric accessors, left otherwise
(`DataTable.tsx:128-133`). Numeric cells get `tabular-nums`
(`DataTable.tsx:434`) so columns line up.

For CSV round-trip safety, set `accessor` even on render-only columns —
otherwise the cell-text extractor returns empty rather than guessing
text out of an arbitrary `ReactNode` (`DataTable.tsx:233-256`).

There's an open `FIXME` for typed row-access; the workaround casts to
`Record<string, unknown>` in three places (issue #57,
`DataTable.tsx:204`, `:215`, `:244`, `:439`).

## ConfirmDialog

`web/src/components/ConfirmDialog.tsx`. Imperative `confirm()` and
`prompt()` primitives that replace every `window.confirm()` /
`window.prompt()` call site (FE HIGH-5).

```tsx
// usage (matches the docstring at ConfirmDialog.tsx:11-17)
const confirm = useConfirm();
if (!await confirm({ message: "Delete this entry?", severity: "danger" })) return;

const prompt = usePrompt();
const name = await prompt({ message: "Preset name?", defaultValue: "" });
if (name === null) return;
```

`<ConfirmDialogProvider>` mounts once at the top of `<App>`
(`web/src/App.tsx:172`). Severity (`default | danger | warning`)
controls button tint and default labels (`ConfirmDialog.tsx:32-44`,
`:178-189`). Esc cancels via a capture-phase keydown listener so app-wide
handlers don't swallow it first (`ConfirmDialog.tsx:88-101`). Outside-click
on the backdrop also cancels (`ConfirmDialog.tsx:196-199`).

## QueryStateBoundary

`web/src/components/QueryStateBoundary.tsx`. Render-time error fallback
for list pages (FE2-001). Pre-fix, a failed list query just resolved to
`data === undefined` and the page rendered as if the workspace were
empty.

```tsx
// web/src/components/QueryStateBoundary.tsx:30-56
<QueryStateBoundary query={partsQuery} resourceLabel="parts">
  <DataTable rows={partsQuery.data ?? []} … />
</QueryStateBoundary>
```

401 is excluded — the `QueryCache.onError` path is bouncing the user to
/login anyway, and a flash of "couldn't load" mid-bounce is confusing
(`QueryStateBoundary.tsx:35-36`).

### `InlineQueryError`

`QueryStateBoundary.tsx:78-110`. Sibling export for panels that mix
action surfaces (forms, buttons) with a query — the boundary version
short-circuits the whole subtree, which would also kill the action UI.
`InlineQueryError` renders a small inline error card in place of the
data block while leaving the surrounding form interactive. Same 401
exclusion rule.

```tsx
<form onSubmit={save}>
  <input … />
  <InlineQueryError query={lookupQuery} label="suggestions" />
  <button type="submit">Save</button>
</form>
```

Reach for `QueryStateBoundary` on list pages, `InlineQueryError` on
detail/form panels (PR #291, issue #245).

## RouteSkeleton

`web/src/components/RouteSkeleton.tsx`. Two variants — `"table"` and
`"form"` — used as `<Suspense>` fallback for lazy-loaded routes (FE2-022).
Uses existing Tailwind tokens (`card`, `bg-panel2`); no new design
tokens. See [routing](routing.md) for where it's wired.

## CommandPalette

`web/src/components/CommandPalette.tsx`. ⌘K / Ctrl+K palette built on
[`cmdk`](https://github.com/pacocoursey/cmdk). Listens for the keyboard
shortcut globally (`CommandPalette.tsx:32-46`) plus a custom event
`stockmgr:openCommandPalette` for in-app triggers (e.g. mobile drawer).

Search query: `useWsKey("cp-search", q)` (`CommandPalette.tsx:48-53`),
gated on `q.trim().length >= 2`, `staleTime: 30_000`. Returns five
groups: parts, storage, projects, lots, orders
(`CommandPalette.tsx:106-178`). The "Navigate" group is hardcoded
(`CommandPalette.tsx:73-104`). Styling for `cmdk`'s `data-*` attributes
lives in `web/src/index.css:96-140`.

## EntityHeader

`web/src/components/EntityHeader.tsx`. The card header used by every
detail page (parts, storage, lots, projects, orders, builds). Slots:

- `title` (required) and optional `subtitle` (`EntityHeader.tsx:60-61`)
- `breadcrumb` rendered above the title (`:41`)
- `idCode` rendered as a monospace pill (`:62-66`)
- `actions` aligned right of the title (`:69`)
- `imageUrl` optional thumbnail, anchored to the full-size view
  (`:44-58`)
- `stats[]` — KPI strip rendered along the bottom border with
  `tone` mapped to `text-text` / `text-danger` / `text-warning` /
  `text-success` (`:23-28`, `:71-83`)

## TrustedParts Attribution

`web/src/components/PoweredByTrustedParts.tsx:9-32` renders the visible,
followable "Powered by TrustedParts" link with `target="_blank"` and
`rel="noopener noreferrer"`; it intentionally omits `nofollow`
(`:23-29`). Use `<PoweredByTrustedParts/>` on every TrustedParts-derived
view; ToU compliance is enforced by review.

`web/src/components/SourcingSourceLabel.tsx:10-29` renders the
TrustedParts source pill with `aria-label="Source: TrustedParts"`
(`:20-23`) so TrustedParts-derived distributor data stays visually and
accessibly labelled.

## Alerts Modal

`web/src/routes/sourcing/alerts/AlertFormModal.tsx` is the shared create/edit
surface for `/sourcing/alerts`, the part Authorized supply shortcut, and the
project Source BOM shortcut. The modal builds the discriminated threshold
payload before calling `web/src/lib/sourcingAlerts.ts`, keeps empty recipients
as `null` so the backend can target workspace admins, and only renders
TrustedParts country/currency/distributor filters for alert types accepted by
the backend sourcing-alert validator.

`web/src/routes/parts/detail/AuthorizedSupplyTab.tsx` mounts the part
detail **Authorized supply** tab. It reads
`GET /api/parts/{part_id}/sourcing`, displays TrustedParts attribution
with `<PoweredByTrustedParts/>`, labels the table with
`<SourcingSourceLabel/>`, filters distributors client-side, and refreshes
with `POST /api/parts/{part_id}/sourcing/refresh`.

The tab also renders TrustedParts gap fields from the same response:
lifecycle and supply-chain risk pills use existing `pill` plus
`text-success` / `text-warning` / `text-danger` / `text-muted` token
classes, tariff and RoHS pills use the same semantic palette, and the
specifications block is a native collapsed `<details>` panel. No new
global utility or pill variant is introduced.

Project Sourcing BOM risk pills follow the same palette and also prefix
Lifecycle, Supply chain, and RoHS status text with `lucide-react` icons.
The icons are `aria-hidden="true"` so assistive tech reads the existing
status text/labels, while greyscale and low-vision users still get a
non-colour severity cue (SA-18 / #510).

## MpnLookup

`web/src/components/MpnLookup.tsx`. Affordance attached to MPN inputs that
POSTs to `/api/parts/lookup-mpn` and hands the populated record back via
`onResult`. Disabled while empty or in-flight (`MpnLookup.tsx:53-62`).
Failures surface inline as a tiny note — network errors are an expected
UX here (`MpnLookup.tsx:44-45`):

```tsx
// web/src/components/MpnLookup.tsx:44-45
} catch (e) {
  setNote(e instanceof ApiError ? e.userMessage : "Lookup failed");
}
```

The actual data source (Mouser / DigiKey / none) is configured per
workspace via Settings → Workspace → Parts data provider; the FE doesn't
care which.

## Scanner family

`web/src/components/scanner/`. Three files:

- `Scanner.tsx` — dispatcher that picks ZXing or Scandit based on the
  workspace setting (`/workspaces/current` returns `scanner` +
  `has_scanner_license_key`).
- `ZxingScanner.tsx` — open-source default.
- `ScanditScanner.tsx` — opt-in commercial backend, requires a
  workspace-scoped license key.

See [scanner](scanner.md) for the full breakdown.

## ChunkLoadErrorBoundary

`web/src/components/ChunkLoadErrorBoundary.tsx`. Catches `ChunkLoadError`
from a stale lazy-import after a deploy, reloads once via
`window.location.reload()`, then shows a retry banner if the second
attempt also fails. See [routing](routing.md) → "Chunk-load error
recovery".

## KiCad 2D previews

`web/src/components/eda/`. `SymbolPreview` and `FootprintPreview` render a
hosted symbol or footprint as KiCad draws it; the CAD tab mounts one under
each picker once a hosted entry is selected
(`routes/parts/detail/PartCad.tsx`, the `renderPreview` prop on `RefSlot`).
Both are thin wrappers over `KicanvasFrame`, which is where everything
awkward about the dependency lives.

**The viewer is vendored and alpha.** KiCanvas ships no npm package, so a
built bundle is checked in at `web/public/kicanvas/`, pinned to an exact
upstream commit, with [kicanvas-provenance](kicanvas-provenance.md) recording the pin, the build command
that reproduces it byte-for-byte, and — more useful day to day — which
`<kicanvas-embed>` attributes actually work at that commit. Read that page before
changing an attribute or bumping the pin; most of the documented API is
marked not-yet-implemented upstream and silently ignored, including every
`kicanvas:*` event.

**It is not in the JS bundle.** The file lives under `public/` rather than
`src/` and is fetched on first mount by `components/eda/kicanvas.ts`, which
appends one module script and memoises the promise. Nothing is downloaded
by a user who never opens the CAD tab, or who opens it without selecting a
hosted entry. `public/` also keeps a minified third-party bundle out of
`eslint src` and out of rollup's input graph — see [kicanvas-provenance](kicanvas-provenance.md).

**Failure is expected, not exceptional.** KiCanvas parses files this app
did not write, and a parse failure on the tab where you configure that very
entry must not take the form with it. `PreviewBoundary` catches and shows a
plain "Preview unavailable" card, and deliberately does *not* re-throw to
the outer Sentry boundary — there is no action to take on "an alpha viewer
could not draw this symbol". A failed bundle fetch degrades the same way.

**The URLs are backend-synthesised.** KiCanvas cannot read `.kicad_sym` or
`.kicad_mod`, so the components point at
`/api/eda/{symbols,footprints}/{id}/preview.kicad_{sch,pcb}` rather than at
the stored file, and the extension in the path is load-bearing — the viewer
types a document by its URL's basename. See [api/eda](../api/eda.md) →
"2D previews".

**A broken preview is silent, so it is pinned from both ends.** A document
KiCanvas cannot parse draws nothing and reports nothing. Two tests make
that loud instead:
`backend/tests/test_eda_preview_fixtures.py` holds the builders to
documents checked in at `backend/tests/fixtures/eda/preview/`, and
`src/components/eda/__tests__/kicanvasContract.test.ts` parses those same
files with KiCanvas's real `KicadSch` / `KicadPCB` — asserting the symbol
keeps its units and pins, the placement resolves its `lib_symbol`, and
every layer a footprint draws on is one the synthetic board declares. The
parsers come from a second, test-only build of the pinned commit at
`web/test-vendor/kicanvas-parsers/`, because the shipped bundle exports
nothing; `kicanvasPin.test.ts` keeps the two builds on the same commit. If
the contract test fails after a bump, the preview is broken — fix the
wrapper, don't relax the test.

## KiCad 3D preview

`web/src/components/eda/`. `ModelPreview` is the 3D sibling of the 2D
previews: the CAD tab mounts one under the footprint's attached 3D models
(`routes/parts/detail/PartCad.tsx`, `ModelPreviewPanel`), with a small
selector when more than one is attached. It renders with three.js.

**three.js is a lazy chunk.** three plus its GLTF/VRML loaders is ~800 KB,
and the tab needs it only once a model is actually opened. Everything that
touches three lives in `components/eda/modelRenderer.ts`, which `ModelPreview`
imports through a dynamic `import("./modelRenderer")` — so Rollup splits it
into its own chunk, absent from the main bundle. `model3dChunk.test.ts` is
the guard: it fails if `three` is imported anywhere else, or if `ModelPreview`
stops loading it lazily (the analogue of `kicanvasPin`'s "out of the module
graph" check). Unlike KiCanvas, three *is* a normal npm dependency
(exact-pinned in `package.json`), not a vendored bundle.

**The URL branches on kind.** STEP has no browser renderer, so it goes
through the server-side conversion route `/api/eda/datafiles/{id}/preview.glb`
(GLB, drawn with `GLTFLoader`). WRL is a mesh three.js reads directly, so it
is fetched straight from `/api/eda/files/{ws}/{sha}.wrl` and drawn with
`VRMLLoader` — no server conversion. See [api/eda](../api/eda.md) → "3D model
preview".

**Failure degrades the same way as the 2D previews.** `ModelPreview` reuses
`PreviewBoundary`, and any fetch/convert/parse failure (a corrupt STEP is a
`422` from the route, an unreadable WRL throws in the loader) shows a plain
"3D preview unavailable" card rather than taking the tab down. jsdom has no
WebGL, so `ModelPreview.dom.test.tsx` mocks `modelRenderer` and pins the
wiring — the right URL and format reach it, and the WebGL context is released
(`dispose`) when the card collapses.

## Smaller helpers

| Component | Source | Purpose |
|---|---|---|
| `Brand` | `Brand.tsx` | App logotype |
| `EmptyState` | `EmptyState.tsx` | Centred icon + headline + CTA for empty list pages |
| `PartsTopNav` | `PartsTopNav.tsx` | Header tab strip for the parts area (Parts / Lots / Stock history) |
| `SubNav` | `SubNav.tsx` | Tab strip for entity detail pages |
| `ThemedToaster` | `ThemedToaster.tsx` | Sonner toaster wired to the active theme |
| `ThemeToggle` | `ThemeToggle.tsx` | system / light / dark switch |
| `ActivityTimeline` | `ActivityTimeline.tsx` | Timeline view for entity activity feeds |
| `AttachmentsPanel` | `AttachmentsPanel.tsx` | Upload + list panel for entity attachments |
| `layout/AppShell` | `layout/AppShell.tsx` | Top nav, mobile drawer, command-palette mount |

## TODO(verify)

- `AttachmentsPanel` (`web/src/components/__tests__/AttachmentsPanel.test.tsx`,
  294 lines) — confirm whether it's reusable across entities or only used
  by the part-detail Attachments tab. The path under `components/` suggests
  reusable, but the test naming is ambiguous.
