# components

Audience: engineer

Reusable presentational + behavioural components shared across pages. Page-specific components live under `web/src/routes/<area>/`.

## Files

| File / dir | What |
|---|---|
| `DataTable.tsx` | The shared table — search, sort, hidden columns, CSV export, multi-select |
| `DataTable.test.tsx` | Co-located unit test for DataTable |
| `Modal.tsx` | Shared accessible dialog shell with focus trap, ESC close, backdrop dismiss, and focus restoration |
| `EntityHeader.tsx` | Standard entity-page header (title / breadcrumbs / actions) |
| `SubNav.tsx` | Tabbed sub-navigation used on entity pages |
| `PartsTopNav.tsx` | Parts-area top nav |
| `AttachmentsPanel.tsx` | Polymorphic attachments UI (upload + list + delete) |
| `ActivityTimeline.tsx` | Activity / audit feed for an entity |
| `MpnLookup.tsx` | MPN provider lookup widget (Mouser / DigiKey) |
| `CommandPalette.tsx` | ⌘K palette |
| `ConfirmDialog.tsx` | Destructive-action confirmation modal |
| `EmptyState.tsx` | Standard empty-state placeholder |
| `RouteSkeleton.tsx` | Suspense fallback for lazy routes |
| `QueryStateBoundary.tsx` | TanStack loading / error boundary wrapper |
| `ChunkLoadErrorBoundary.tsx` | Catches stale-chunk errors after deploy → reload |
| `ThemeToggle.tsx`, `ThemedToaster.tsx` | Theme toggle + sonner toaster wired to theme |
| `Brand.tsx` | App logo / wordmark |
| `layout/AppShell.tsx` | App chrome — sidebar, top bar, route outlet |
| `scanner/Scanner.tsx`, `ZxingScanner.tsx`, `ScanditScanner.tsx` | Dual-engine barcode scanner |
| `eda/SymbolPreview.tsx`, `FootprintPreview.tsx` | KiCad 2D previews of a hosted symbol / footprint |
| `__tests__/`, `__dom__/` | Test trees |

## Public surface

Every component is its default / named export. The most commonly reused:

| Component | Use for |
|---|---|
| `DataTable` | Any table — before rolling your own |
| `Modal` | Dialogs that need `role="dialog"`, `aria-modal`, focus trapping, ESC close, backdrop dismiss, and trigger focus restoration |
| `EntityHeader` + `SubNav` | Entity pages (Part, Order, Build, …) |
| `AttachmentsPanel` | Any entity that supports attachments |
| `ConfirmDialog` | Any destructive action |
| `Scanner` | Anywhere a barcode scan is requested |

## Hard rules (this module)

1. **Use `DataTable` before adding a new table.** It already does search, sort, hidden columns, CSV export, multi-select. See [components](../../../docs/frontend/components.md).
2. **Use `Modal` before adding a route-local dialog shell.** API: `open`, `onClose`, `title`, `children`, optional `initialFocusRef`, `size`, and `className` for the panel.
3. **Use the `index.css` utility set** (`btn`, `card`, `pill`, `input`, …) before adding new ones. See [tailwind-utilities](../../../docs/frontend/tailwind-utilities.md).
4. **Lazy routes wrap their suspense in `RouteSkeleton`** and their data in `QueryStateBoundary` for uniform loading / error UX.
5. **2D CAD previews are server-rendered SVGs shown via `<img>`** (`eda/SvgPreview.tsx`) — kicad-cli draws them in the `kicad-render` sidecar (see `render/`, ADR-0032). The `<img>` is deliberate: an SVG from attacker-supplied geometry can run script if inlined, and an `<img>` never does. The 3D preview keeps its own lazy three.js chunk (`eda/modelRenderer.ts`).

## See also

- [components](../../../docs/frontend/components.md) — DataTable + reusable component catalog
- [tailwind-utilities](../../../docs/frontend/tailwind-utilities.md) — utility class set
- [scanner](../../../docs/frontend/scanner.md) — ZXing / Scandit dual-mode

## Don't

- Don't roll a new table — extend `DataTable` (or open an issue) before adding a one-off.
- Don't call `fetch` from a component — go through `lib/api.ts` (see `web/src/lib/README.md`).
- Don't import from `web/src/routes/<area>/` here; `components/` is the *shared* tree.
