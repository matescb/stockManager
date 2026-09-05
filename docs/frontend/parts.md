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
- **Below `xl` a row click navigates, exactly as it did before the pane
  existed.** The breakpoint is checked in TypeScript as well as CSS —
  `useIsXlViewport` (`web/src/lib/useMediaQuery.ts`, `XL_VIEWPORT_QUERY`) —
  because a `hidden xl:flex` pane alone would leave the click handler selecting
  something the user cannot see. Keep the hook's query and the pane's Tailwind
  prefix in sync.
- **The preview waits for `xl` while the category rail appears at `lg`.** The
  route is three columns: rail (`w-56`), table, pane (`w-80`, widening to
  `w-96` only at `2xl`). With the 240px app sidebar and page padding counted, a
  pane at `lg` would leave the table 176px; at `xl` it gets 432px, at 1440px
  592px, at `2xl` 688px. The rail lives in an outer flex row (`PartsList.tsx`)
  and `PartsPreviewLayout` splits the column inside it, so the two nest without
  either knowing about the other.
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
