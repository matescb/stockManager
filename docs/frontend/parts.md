# Parts Frontend

Audience: engineer

Part-detail UI flows that are larger than one tab component.

## Authorized Supply To Order

The Authorized-supply tab renders one `Add to order` action per distributor row and opens `CreateOrderLineModal`, which lists draft orders with `GET /api/orders?order_status=draft` and submits through the documented order endpoints. Existing draft orders receive a line via `POST /api/orders/{order_id}/entries`; the create-new branch posts `POST /api/orders` with one initial entry. The saved entry `comments` value is the compliance-safe TrustedParts summary only; the distributor page is available as a modal link but is not persisted in comments. Source: `web/src/routes/parts/detail/AuthorizedSupplyTab.tsx:318-343`, `web/src/routes/parts/detail/CreateOrderLineModal.tsx:82-154`.

## Parts List Preview Pane

Clicking a row in `/parts` opens a preview beside the table instead of
navigating: `/parts?sel=<part id>`. The selection composes with the category
filter's `?category=` — both writers use a functional `setSearchParams` updater
over the previous params, so neither drops the other's key. The 17 `/parts/:partId/*` detail routes are
untouched and remain the destination for real work — the pane links to
`/parts/:id/info` under "Open full page".

Three rules the implementation depends on:

- **Selection lives in the URL.** `usePartPreview`
  (`web/src/routes/parts/preview/usePartPreview.ts`) reads and writes the `sel`
  search param, so a selected part stays linkable and back/forward work without
  extra state. Clicking or pressing Enter *pushes* a history entry; arrow-keying
  down the rows *replaces* one, so browsing twenty rows does not cost twenty
  Back presses.
- **Below the pane's breakpoint a row click navigates, exactly as it did before
  the pane existed.** The breakpoint is checked in TypeScript as well as CSS —
  `useIsXlViewport` / `useIsLgViewport` (`web/src/lib/useMediaQuery.ts`,
  `XL_VIEWPORT_QUERY` / `LG_VIEWPORT_QUERY`) — because a `hidden xl:flex` pane
  alone would leave the click handler selecting something the user cannot see.
  `usePartPreview` returns both halves of that decision (`canPreview` and
  `paneBreakpoint`) from one branch, and `PartPreviewPane` takes its Tailwind
  prefix from `paneBreakpoint`, so the two cannot drift.
- **The preview waits for `xl`, or `lg` once the category rail is collapsed.**
  The route is three columns: rail (`w-56`), table, pane (`w-80`, widening to
  `w-96` only at `2xl`). With the 240px app sidebar and page padding counted, a
  pane at `lg` would leave the table 176px; at `xl` it gets 432px, at 1440px
  592px, at `2xl` 688px. Collapsing the rail to its 44px strip gives most of
  that back, so the pane is allowed forward to `lg` — the table still gets
  356px, and more again once the app sidebar is collapsed too. The rail lives
  in an outer flex row (`PartsList.tsx`) and `PartsPreviewLayout` splits the
  column inside it, so the two nest without either knowing about the other;
  the rail's collapse state is owned by `PartsList` precisely because the
  pane's breakpoint depends on it.
- **The pane paints before it fetches.** `GET /parts` returns whole part
  objects, so the clicked row is handed to `PartPreviewPane` as `fallbackRow`
  and used as TanStack's `placeholderData`. The fetch only corrects it. All
  three of the pane's queries reuse keys the detail pages already use
  (`part`, `part`/`stock`, `storage`), so the preview warms the full page.

`DataTable` gained one optional prop for this, `onRowFocusChange`, fired when
Arrow Up/Down moves row focus. It is opt-in: a table that does not pass it keeps
the arrow-key behaviour it always had.

Source: `web/src/routes/parts/preview/`,
`web/src/routes/parts/__dom__/PartsList.preview.dom.test.tsx`.

### Collapsing the category rail

The rail has a toggle in its header that collapses it to a 44px strip
carrying only the re-open button. The choice is remembered per workspace in
`localStorage` via `usePanelCollapse` (`web/src/lib/usePanelCollapse.ts`),
which mirrors `DataTable`'s persistence — same `ws:<id>:…` key shape, same
fall-back-to-default read, same adopt-the-new-value-on-workspace-switch
effect. Every access is wrapped in `try`/`catch`: `localStorage` throws
outright in Safari's private mode, and a thrown preference must degrade to
"the panel works, the choice isn't remembered".

Anything unusable in storage — absent, empty, malformed, wrong type — reads
as expanded, so there is no way to end up collapsed with no way back. The
collapsed strip's button names the active filter (`Show categories (filtered
by Passives)`) because with the tree gone it is the only thing left that can
explain a short list.

The app sidebar (`web/src/components/layout/AppShell.tsx`) collapses through
the same hook, independently, to a 64px icon rail. Every collapse class there
is `lg:`-prefixed so the sub-`lg` mobile drawer keeps its full labelled width.

Source: `web/src/routes/parts/__dom__/PartsList.collapse.dom.test.tsx`,
`web/src/components/layout/__dom__/AppShell.collapse.dom.test.tsx`,
`web/src/lib/__dom__/usePanelCollapse.dom.test.tsx`.
