# Frontend Teardown

Scope: `web/src` — routes, components, `lib/`, scanners, `tailwind.config.js`, `Dockerfile.prod` (bundle/runtime only).
Date: 2026-05-01.
Existing review IDs covered/extended: FE-001..FE-008 (see `docs/claude-review-issues.md`). New findings use the `FE2-NNN` namespace.

## Frontend Issues

### FE2-001: No global 401 handler — every list silently empties on session expiry

Severity: **Critical**

Evidence:
- `web/src/lib/api.ts:36-52` — `rawRequest` throws `ApiError(401, ...)` but does nothing else; the auth context only consumes 401 inside `refresh()` (`web/src/lib/auth.tsx:43-50`).
- `web/src/main.tsx:18-20` — `QueryClient` is created with `retry: false` and no `QueryCache` `onError`, so `useQuery` errors never reach `AuthProvider`.
- `web/src/App.tsx:114-123` — `<Gate>` only consults `me` once at boot. After login expires the user keeps seeing stale rendered pages with empty `data ?? []` lists (also FE-001).
- 30+ list/detail pages (`OrdersList`, `LotsList`, `BuildsList`, `BuildDetail`, `OrderDetail`, `PartLayout`, `ProjectLayout`, `LotDetail`, `StorageDetail`, `Reports`, `ActivityTimeline`, `AttachmentsPanel`, etc.) ignore `isError` entirely. The cookie can have expired five minutes ago and the UI still claims "No orders yet — Create a purchase order".

Impact:

When a user's session expires mid-session (or any non-trivial backend incident occurs), the entire app pretends data was deleted instead of bouncing to `/login`. A second class of bug: 5xx, 502, network drops all render as "no data" with no retry affordance. Users will misdiagnose as data loss and call ops.

Fix instruction:

In `main.tsx`, install a `QueryCache({ onError })` that on `ApiError` with `status === 401` clears `me`/`workspaceId` and forces `<Navigate to="/login" replace state={{ from: location }} />` (and have `<Login>` honour `state.from` so deep-link is preserved). Add a uniform `<QueryStateBoundary>` (or destructure `isError` everywhere) that renders a retry banner instead of silent empty. Pin with a Vitest test that mocks fetch with a 401 and asserts the redirect.

### FE2-002: AppShell workspace `<select>` switches workspace on mere change events, with no confirm — single-keypress data leak risk

Severity: **High**

Evidence:
- `web/src/components/layout/AppShell.tsx:80-89` — `<select onChange={(e) => switchWorkspace(e.target.value)}>` fires on keyboard arrow-down, on screen-reader value-change, and on touch.
- `web/src/lib/auth.tsx:67-72` — `switchWorkspace` POSTs `/workspaces/{id}/switch` and **calls `window.location.reload()`** — there is no confirmation, no in-flight indicator, and no `disabled` while pending.
- A user typing into another input on the page can still land focus on the workspace selector via tab; an arrow key then silently switches workspaces.

Impact:

In a multi-workspace account (the default org install), a single accidental keystroke flips global workspace state and triggers a full reload, throwing away unsaved form data anywhere in the tree (Add stock, Receive, BOM mapping, ScanImport queue, etc.). This is also the only place a workspace switch happens, so there is no recovery — the user has to retype everything.

Fix instruction:

Replace the `<select>` with a `<button>`-driven dropdown menu that requires an explicit click on a target workspace, and (when the current page has dirty form state) prompts via `useConfirm` before switching. Also reset the React Query cache via `qc.clear()` instead of `window.location.reload()` so unrelated tabs don't reload.

### FE2-003: Workspace switch does not flush React Query cache before reload — cross-tenant data flashes on next render

Severity: **High**

Evidence:
- `web/src/lib/auth.tsx:67-72` — `switchWorkspace` does `localStorage.setItem("workspaceId", id); setWorkspaceId(id); window.location.reload();`. No `queryClient.clear()`, no cookie wait, no membership re-validation.
- `web/src/main.tsx:18-20` — the `QueryClient` is module-scoped; without a clear, the old workspace's `["parts"]` / `["orders"]` data sits in memory until reload completes.
- Between `setWorkspaceId` and `window.location.reload()`, any in-flight component render reads cached data from the **previous** workspace while `workspaceId` already points at the new one. `BrowserRouter` does not unmount synchronously on a reload.
- The session/workspace cookie is set by the POST response; the page reload races the cookie write — on slow networks the reloaded SPA can call `/auth/me` before the new cookie lands.

Impact:

Brief but real cross-workspace data flash, plus a harder-to-debug "I just switched workspace and my parts list is wrong" race when the reload outpaces the cookie write. Workspace isolation is the load-bearing security invariant of the app (see CLAUDE.md), and the FE switch path treats it as best-effort.

Fix instruction:

Make `switchWorkspace` `await` the API response, then call `queryClient.clear()`, then `setWorkspaceId(id)`, then `nav("/parts", { replace: true })` — no `window.location.reload()`. Server-side, the workspace cookie should be set on the same response; on the FE, gate Outlet rendering on `workspaceId !== null` (and ideally include `workspaceId` in every `queryKey`, see FE2-004).

### FE2-004: TanStack query keys do not include workspace — every query is implicitly cross-tenant

Severity: **High**

Evidence:
- `web/src/lib/auth.tsx:67-72` — workspace switch reloads the page, which is the only thing keeping cached `["parts"]`/`["orders"]` from leaking across workspaces.
- Every query key in the app is workspace-agnostic: `["parts"]`, `["orders", { archived }]`, `["storage"]`, `["part", id]`, `["lots"]`, `["report", "low-stock"]`, … (search shows ~15 different `queryKey: ["parts"]` callsites in `routes/`).
- `lib/api.ts` does not append the workspace id to URLs (it relies on the cookie / X-Workspace-Id header set elsewhere).

Impact:

Without a workspace dimension in the cache key, the moment workspace switching is fixed to *not* full-reload (FE2-003), TanStack Query will happily serve workspace-A data to workspace-B. This is the same "workspace isolation enforced in code, not the DB" risk on the client side: **every** invalidation/refresh is an audit boundary, and the cache currently has none.

Fix instruction:

Add `workspaceId` (read from `useAuth()`) as the first element of every query key — `["ws", workspaceId, "parts", { archived }]`. Centralise it via a `wsKey(...)` helper to make this hard to forget. Adding `workspaceId` to `useQuery` keys also lets `qc.invalidateQueries({ queryKey: ["ws", workspaceId] })` give a clean per-tenant flush.

### FE2-005: Bulk-delete dialog in `PartsList` is a hand-rolled modal that bypasses `<ConfirmDialog>`, with no focus trap or Esc handling

Severity: **High**

Evidence:
- `web/src/routes/parts/PartsList.tsx:126-176` — re-implements an overlay + card with `onClick` outside-to-close. No `aria-modal`, no `aria-labelledby`, no focus trap, no Esc handler, no focus restoration to the originating button.
- The dialog has the only **destructive bulk action** in the app and is the worst place to skip the existing primitive (`web/src/components/ConfirmDialog.tsx`).
- The `OrderDetail` "Add Line" inline form (`web/src/routes/orders/OrderDetail.tsx:163-188`) and `BuildDetail` consumption modal-ish editor have similar one-off shapes.

Impact:

Keyboard users cannot Esc out of an archive-many-parts dialog. Screen readers read the page behind it. Accidental click outside (or overlay flicker) does not cancel. Nothing focuses the destructive "Archive" button at open, so Enter does whatever the previous focus did. The dialog falls behind hand-rolled overlay z-index battles in the future as the UI grows.

Fix instruction:

Replace this dialog (and any similar one-offs) with `useConfirm` from `web/src/components/ConfirmDialog.tsx`, supplying `severity: "danger"` and a `message` that includes the part-name preview list. Then harden `ConfirmDialog` itself: add a focus trap (or use `<dialog>`'s native modal), restore focus to the originating element on close, and add `aria-labelledby` referencing the title.

### FE2-006: Mutations have no `useMutation` — every form keeps its own `busy` flag, no rollback on failure, double-submit possible across tabs

Severity: **High**

Evidence:
- 0 calls to `useMutation` across the entire `web/src` tree (grep confirms only `useQuery` is used).
- Every form rolls its own state: `PartCreate.tsx:62`, `PartAddStock.tsx:22`, `PartRemoveStock.tsx:42`, `PartMoveStock.tsx:42`, `OrderDetail.tsx:41-47`, `BuildDetail.tsx:41-42`, `WorkspaceSettings.tsx:60-62`, `ScanImport.tsx:163`, `ProjectImport.tsx:89-90`, etc.
- No optimistic updates, no rollback, no `mutationKey` to dedupe concurrent submits across components, no retry policy.
- `OrderDetail.addEntry` (`OrderDetail.tsx:48-67`) does not gate on `busy`, so double-clicking the Add button while the network is slow posts twice — order entries are append-only on the server.

Impact:

Every mutation site is the same hand-built loading/error/success cycle, drifting in subtle ways (some toast, some inline `setErr`, some both). Double-submits are real on slow networks. There is no place to attach a single auth-error handler. Adding a 422-aware error parser means touching 30+ files.

Fix instruction:

Introduce a small set of `useMutation` wrappers and migrate forms one resource at a time (orders entry, parts CRUD, stock add/remove/move, BOM import, workspace settings). Use `mutationKey` to disable concurrent submits. Add optimistic updates only where the server returns the resulting object (otherwise just invalidate). Pin double-submit fix with a Vitest that triggers two concurrent submits and expects exactly one POST.

### FE2-007: DataTable selection set silently leaks `id`s of rows that no longer exist after refetch / filter change

Severity: **High**

Evidence:
- `web/src/components/DataTable.tsx:86` — `selected` is a `Set<string>` that only ever has entries added/removed via `toggleSelected`/`toggleAllVisible`.
- No `useEffect([rows])` to drop ids that are no longer in `rows`. After parent invalidates `["parts"]`, deleted/archived rows still report as "selected" and their ids are passed to `selectionAccessory` — and from there straight into `POST /parts/bulk-delete` (`PartsList.tsx:31-32`).
- `toggleAllVisible` (DataTable.tsx:155-165) operates on `visibleIds` (the search-filtered set). Selecting all, narrowing the search, then clicking "Delete" sends ids that aren't visible anymore. The current `setConfirming({ ids, clear })` snapshot mitigates only some of this.
- `clearSelection` is only called by the consumer's success handler. A bulk-delete that 500s silently leaves the now-archived ids selected.

Impact:

Bulk-archive can act on rows the user can't see (selected on a previous filter then narrowed). After refetch, ids of archived rows linger in `selected` and re-archiving them will 404 / 409 on the server — the user gets a partial-success toast they didn't expect.

Fix instruction:

Inside `DataTable`, add `useEffect` keyed on `rows` that prunes `selected` to `selected ∩ rows.map(rowKey)`. Also clear selection on `tableId` change. Document that `selectionAccessory` only ever sees ids present in the current `rows`. Add a Vitest covering: select rows → search filter → "Delete" only acts on visible.

### FE2-008: DataTable CSV export does not handle embedded CRLF, omits BOM, and serialises React renders to `[object Object]`

Severity: **High**

Evidence:
- `web/src/components/DataTable.tsx:134-149` — quotes only doubled `"`, joins lines with `"\n"` (LF only), no `\r\n`, no UTF-8 BOM. `String(v ?? "")` is called directly on values, so a column whose accessor returns nothing but has a `render` function falls back to `String((r as any)[c.key])` — producing `[object Object]` for any nested-object value.
- `MIME` is `text/csv;charset=utf-8` but `﻿` BOM is missing — Excel (Windows, the actual user base for stock workbooks) opens UTF-8 CSV without BOM as Latin-1 and mangles non-ASCII manufacturer names, MPNs with diacritics, and currency symbols.
- The blob URL from `URL.createObjectURL(blob)` is never `URL.revokeObjectURL`-ed, so every export leaks a blob into memory until reload.
- A cell containing `\r\n` survives unquoted-newline-aware parsers only because the field is double-quoted, but a field containing exactly one `"` followed by `,` produces malformed output (the doubling is correct, but Excel still complains on import for cells beginning with `=`/`+`/`-` — formula injection vector).
- The export ignores selection: it always exports `sorted` (current filter+sort), with no UI hint. This is fine but undocumented.

Impact:

Operators export "stock value" / "low stock" CSVs into Excel and silently get wrong manufacturer names, broken pricing rows, and an attacker-controlled cell starting with `=HYPERLINK(...)` becomes a live formula on open (the part's user-entered comments / specs flow into CSV unsanitised). Memory leak from unrevoked blob URLs is minor but real on power-user sessions.

Fix instruction:

Prepend `﻿` to the blob, switch line terminator to `\r\n`, prefix any cell starting with `= + - @` with a leading `'` (Excel formula-injection mitigation), revoke the URL with `URL.revokeObjectURL` after click, fall back to `c.render` rendering text-only when accessor is missing (or require `accessor` for exportable columns). Add a Vitest covering all four cases: BOM present, embedded CRLF round-trips, leading-`=` neutralised, accessor-less columns export blank not `[object Object]`.

### FE2-009: Scanner `Scanner.tsx` and `ZxingScanner.tsx` reference design tokens that Tailwind does not generate

Severity: **High**

Evidence:
- `web/src/components/scanner/Scanner.tsx:57-58` — uses `bg-bg-soft` and `text-text-muted`.
- `web/src/components/scanner/ZxingScanner.tsx:456` — uses `bg-bg-soft`.
- `web/tailwind.config.js:11-25` — defines `bg`, `panel`, `panel2`, `muted`, `text`, `accent`, … but no `bg-soft` or `text-muted` variants on a compound prefix. `bg-bg-soft` resolves to *no class generated* (Tailwind silently produces nothing).
- `web/src/index.css` has no `--c-bg-soft` or `--c-text-muted` variable.

Impact:

The "license missing" / "permission denied" panels render as transparent-on-camera-feed (or full-bleed black-on-black, because the `<video>` element is below). On phones in poor light, the user sees the camera with a tiny error string overlaid and no idea what to do. Extends FE-006 — adds: this is in two more files than the original review found, and the same panel that's misstyled is the only one that explains how to grant camera permission.

Fix instruction:

Replace `bg-bg-soft` with `bg-panel2` and `text-text-muted` with `text-muted` (the actually-defined tokens). Or, if a softer surface is wanted, add `--c-bg-soft` to `index.css` and a matching `bgSoft` color in `tailwind.config.js`. Add a tiny Vitest snapshot that asserts both panels render with a non-empty computed background.

### FE2-010: Login flow loses deep-link target — every redirect after auth dumps the user on `/parts`

Severity: **Medium**

Evidence:
- `web/src/App.tsx:117` — `<Gate>` calls `<Navigate to="/login" replace />` with no `state={{ from: location }}`.
- `web/src/routes/auth/Login.tsx:22` — on success calls `nav("/parts")` unconditionally; ignores any incoming state.
- `web/src/routes/auth/Signup.tsx:29` — same: `nav("/parts")`.

Impact:

A user pasting `https://app/orders/{id}` while logged out (most common: a "received order" notification email link, an attachment URL shared in chat) signs in and lands on the parts list with no idea where their target was. Combined with FE2-001 (silent 401 on stale sessions), users get the same dead-end on session expiry mid-task.

Fix instruction:

In `Gate`, pass `state={{ from: location.pathname + location.search }}` to `<Navigate>`. In `Login` (and `Signup`), read it via `useLocation().state?.from` and `nav(from || "/parts", { replace: true })`. Pin with a route test.

### FE2-011: PartCreate "lookup → create → refresh-from-provider" does three sequential round-trips with no rollback

Severity: **Medium**

Evidence:
- `web/src/routes/parts/PartCreate.tsx:67-83` — flow: `api.post("/parts", ...)` → `api.post("/parts/{id}/refresh-from-provider")`. The refresh is wrapped in a swallow-all `try/catch`; the comment says "non-fatal" but failures are also silent (no toast, no per-part flag).
- The 409-on-MPN handler (`PartCreate.tsx:84-92`) returns early but does not clear `hasLookup`/`specs`/`imageUrl` state, so retrying with a new MPN re-applies the previous lookup's specs to the wrong part.
- If `refresh-from-provider` 5xx's after the part is created, the user lands on `PartInfo` with a part missing all provider-side data and no UI hint that a refresh is needed.

Impact:

Half-created parts show up in the library with the correct identity but no specs, image, or datasheet — and the user doesn't know it's their job to click Refresh. State leaks across MPN attempts cause the lookup preview to lie about a different MPN.

Fix instruction:

On 409, also reset `setDatasheetUrl(null)`, `setImageUrl(null)`, `setSpecs([])`, `setHasLookup(false)`. After the create+refresh combo, surface an inline "Provider refresh failed — retry?" banner if the second call rejected, instead of silently swallowing. Better: a single backend `POST /parts/with-lookup` that does both atomically.

### FE2-012: Date and number formatting use `toLocaleString()` / `toLocaleDateString()` with no fixed locale — server reports drift between operators

Severity: **Medium**

Evidence:
- 14 callsites use `new Date(...).toLocaleString()` / `.toLocaleDateString()` (`StockHistory.tsx:32`, `OrdersList.tsx:78`, `LotsList.tsx:38`, `PartHistory.tsx:27`, `LotDetail.tsx:127`, `BuildDetail.tsx:155`, `Reports.tsx:430`, `ProjectsList.tsx:47`, `PartLots.tsx:26`, `BuildsList.tsx:75`, `AttachmentsPanel.tsx:159`, `ProjectBuilds.tsx:43`, `ActivityTimeline.tsx:170`, `PartSourcing.tsx:76`).
- `Reports.tsx:159` — `c.value.toFixed(2)` — fixed dp regardless of currency (JPY shouldn't have decimals).
- No central `formatDate(iso)` / `formatMoney(value, currency)` helper.

Impact:

A US user and an EU user looking at the same exported "Stock value" CSV see different month/day order, decimal/comma separators, and non-comparable strings. The stock-value report shows JPY with two decimals (".00 JPY"), which is wrong. CSV exports inherit the locale of whoever clicked Export, so cross-team sharing is brittle.

Fix instruction:

Add `web/src/lib/format.ts` with `formatDate`, `formatDateTime`, `formatMoney(value, currency)`, all using `Intl.DateTimeFormat`/`Intl.NumberFormat` pinned to a single workspace-level locale (default `en-US`, surface as a workspace setting). Replace the 14 sites incrementally. CSV export should use ISO-8601 dates regardless of UI locale.

### FE2-013: Form-state resets on entity change still missing in several places — extends FE-004

Severity: **Medium**

Evidence:
- Original FE-004 named `PartSettings` and `ProjectData`. Same anti-pattern still present in:
  - `web/src/routes/projects/detail/ProjectData.tsx:9-12` — three `useState(project.<field>)` initialisers, no reset effect on `project.id` change.
  - `web/src/routes/parts/detail/PartSettings.tsx:12-18` — seven `useState(part.<field>)` initialisers, no reset.
  - `web/src/routes/orders/OrderCreate.tsx` doesn't have this bug (it's a create form), but `OrderDetail.tsx:32-41` keeps `receiveLines`, `receivedOn`, `adding`, `newPartId`, etc. across navigations between order ids when the layout doesn't unmount.
  - `web/src/routes/builds/BuildDetail.tsx:40` — `plan` state survives navigation between builds.

Extends FE-004 — adds: list of remaining sites and the build/order navigations that preserve consumption-plan / receive-line state across entities.

Impact:

User edits part A's settings, navigates straight to part B's settings, types one field, hits Save: silently saves part-A data to part-B record. Same for build consumption plans (the plan keys are project-entry ids that aren't valid for the new build).

Fix instruction:

For each detail layout, key the routed `<Outlet>` child by entity id at the route element (`<Route path="settings" element={<PartSettings key={partId} />} />`), or add a `useEffect([entity.id])` that resets local state. The keying approach is one line per route and is unambiguous.

### FE2-014: Scanner license-key fetch query is never invalidated on workspace switch and never expires

Severity: **Medium**

Evidence:
- `web/src/components/scanner/Scanner.tsx:105-108` — `useQuery({ queryKey: ["ws", "scanner", "license-key"] })` — no workspace id in the key, no `staleTime`/`gcTime` cap.
- `WorkspaceSettings.tsx:455` does invalidate `["ws", "scanner", "license-key"]` after the user pastes a new key, so the same-tab path works. But:
  - Workspace switch (`auth.tsx:67-72`) only does `window.location.reload()`. Until the reload completes the cached license key from the previous workspace is in memory and would be reused if the scanner page rendered.
  - The license key sits in TanStack Query's in-memory cache indefinitely — and `LICENSED_SYMBOLOGIES` Scandit's wasm pulls embeds the key in the SDK's internal state. A long-lived tab keeps a credential that the user thinks "expired".

Impact:

License key is a paid third-party credential (see SEC-005 in the existing review for the related backend bug). The FE caches it more aggressively than necessary and ties cache lifetime to "until window.location.reload happens". Combined with FE2-003/FE2-004, this is the shape of a workspace-isolation bug.

Fix instruction:

Include workspace id in the queryKey (`["ws", workspaceId, "scanner", "license-key"]`), set `gcTime: 0` so the key isn't kept after the scanner unmounts, and never log it (currently `error` is rendered as plain text — confirm Sentry's beforeSend strips it).

### FE2-015: BOM import uploads the entire file contents as base64 inside the JSON body — no client-side size cap

Severity: **Medium**

Evidence:
- `web/src/routes/projects/detail/ProjectImport.tsx:66-72` — `fileToBase64` reads `file.arrayBuffer()` and `String.fromCharCode`s every byte, then `btoa`s the whole thing. No `accept` size enforcement, no warning, no progress indicator.
- `ProjectImport.tsx:219-225` — `<input type="file" accept=".csv,.tsv,.txt">`. No `maxSize` check before reading the bytes; no MIME validation (only file-extension hint).
- The base64 body inflates by 33% before hitting the wire; SEC-007 in the existing review already flagged the server side has no cap.
- Reading a 200 MB file via `String.fromCharCode(...)` in a loop crashes the tab on iPad (Safari mobile).

Impact:

A user picking the wrong file (a build artifact, a 1 GB log) freezes the browser tab during read. Combined with SEC-007, the request also OOMs the backend. There is no progress indicator, so the user thinks the page is broken and reloads — the file has half-uploaded and is left as an orphaned half-decoded payload on the server.

Fix instruction:

Add a 5 MB hard cap in the FE before calling `fileToBase64` (`if (f.size > 5_242_880) toast.error(...)`). Switch from base64-in-JSON to multipart upload via `api.upload`, which is what every other file path in the codebase uses (`AttachmentsPanel.tsx:60`). Add a `<progress>` driven from a `FileReader.onprogress` handler.

### FE2-016: AttachmentsPanel has no client-side type/size guard, allowing 5 GB uploads with no warning

Severity: **Medium**

Evidence:
- `web/src/components/AttachmentsPanel.tsx:48-70` — `doUpload` posts the FormData as-is with no `file.size` or `file.type` check.
- The `<input type="file">` (`AttachmentsPanel.tsx:113-117`) has no `accept` filter; the type dropdown ("datasheet/invoice/image/cad/bom/other") is purely metadata and does not constrain the picker.
- A drag-and-drop of an `.iso` file works fine on the FE. Whatever the backend cap is, the UX for hitting it is a generic toast on a request that took 30s.

Impact:

A user dragging a misnamed 4 GB Solidworks part into a "datasheet" upload uploads silently for minutes before the server rejects (or accepts and stores it forever — depends on backend). No client-side filter means no explanation in the UI; the toast just says "Upload failed".

Fix instruction:

Add `MAX_BYTES` (e.g. 50 MB) and `ALLOWED_MIME_FOR_TYPE: Record<FileType, string[]>` lookups; reject early with a toast naming the chosen `file_type`'s allowed extensions. Add `accept` on the `<input>` keyed off the dropdown selection. This is purely client-side polish; the backend cap remains authoritative.

### FE2-017: `qc.invalidateQueries()` (no key) used in 11 places — invalidates *every* query, including unrelated ones

Severity: **Medium**

Evidence (from grep):
- `routes/parts/detail/PartOther.tsx:14`, `PartOther.tsx:19`
- `routes/storage/StorageDetail.tsx:148`, `StorageDetail.tsx:153`
- `routes/settings/Account.tsx:26`
- `routes/lots/LotDetail.tsx:67`, `LotDetail.tsx:100`
- `routes/settings/Workspace.tsx:69`
- `routes/projects/detail/ProjectOther.tsx:12`, `ProjectOther.tsx:17`

Impact:

Archive/restore/move/adjust on a single lot or storage location triggers refetches of every cached query — `["parts"]` (potentially 1000-row payload), `["lots"]`, `["orders"]`, `["builds"]`, every report, every per-part stock breakdown. Network and DB load multiply by ~the number of mounted screens. On a multi-tab session the parent tab wakes up too. This is also the only thing currently keeping cross-workspace cache hygiene tolerable (FE2-004).

Fix instruction:

Replace each unscoped `invalidateQueries()` with the narrowest prefix that covers the actual mutation. E.g. archive-storage should invalidate `["storage"]` and `["report", "stock-value"]`, not "everything". Add a `lib/queryKeys.ts` central registry to make this enforceable.

### FE2-018: ScanImport queue lives only in component state — full-screen reload, route navigation, or workspace switch wipes scanned-but-unimported rows

Severity: **Medium**

Evidence:
- `web/src/routes/parts/ScanImport.tsx:158-202` — `rows` is a `useState` array; `seenSigs` / `seenMpns` are refs. No persistence to `localStorage` / `sessionStorage`, no "you have N unsaved scans" guard on `beforeunload`.
- The page is the documented happy-path for "operator scans 50 bags from a Mouser delivery" — it's the most session-fragile workflow.
- A click on the workspace selector (FE2-002) reloads and discards everything.

Impact:

A misclick on the workspace selector, a tab refresh, or accidentally hitting Back loses 5-30 minutes of scanning work with no undo. The operator has to re-scan every bag.

Fix instruction:

Persist `rows` to `sessionStorage` (keyed by workspace id) on every change. On mount, restore. Add a `window.beforeunload` handler that warns if `rows.length > 0 && importable.length > 0`. Better still: post each scan to a per-user "scan draft" endpoint that's a server-side queue, so a phone-up-die during scanning doesn't lose data.

### FE2-019: Auth pages don't check `me` and bounce already-authenticated users back

Severity: **Low**

Evidence:
- `web/src/App.tsx:133-134` — `<Route path="/login" element={<Login />} />` is *outside* the `<Gate>` route. There's no inverse gate that redirects to `/parts` if `me != null`.
- A logged-in user navigating to `/login` gets a fresh login form. Submitting it works (back-end happily creates a second session and leaves the first orphaned), since `auth/login` doesn't 409 on already-authed users.

Impact:

Minor UX wart, plus a small session-table bloat path. More annoying when the user opens the app from a bookmark to `/login` after they're already signed in, sees the form, and re-types credentials.

Fix instruction:

Wrap `<Login />` and `<Signup />` in a small `<AuthOnly>` component that does `if (me) return <Navigate to="/parts" replace />`. Trivially testable.

### FE2-020: Error states leak server stack traces / pydantic detail strings into UI

Severity: **Low**

Evidence:
- `web/src/lib/api.ts:48` — `const msg = body?.status?.message || res.statusText;` — raw server message goes straight into `ApiError.message`.
- Almost every `catch (e) { setErr(e instanceof ApiError ? e.message : "Failed") }` in routes (e.g. `PartCreate.tsx:93`, `OrderCreate.tsx:32`, `BuildCreate.tsx:34`, `OrderDetail.tsx:65,108`) renders that string verbatim in a red banner.
- For 422 ValidationError responses the backend's pydantic message ("List should have at most 1000 items, the number of items is 2451") leaks DB internals like column names, table count, etc.
- `lib/api.ts` has no whitelist of safe-to-show categories; anything in the `status.message` lands in the UI.

Impact:

In normal use, low-impact. In incidents, the user sees stack-trace fragments and column names. Combined with Sentry being too eager (SEC-002) this is also a risk for screen-shots flowing back into ticketing systems.

Fix instruction:

In `ApiError`, store the raw message but surface a curated `userMessage` based on `body.status.category` (`unauthenticated`, `forbidden`, `not_found`, `conflict`, `validation_error`, `server_error`). Keep raw `message` for Sentry / `console.error`. Update the standard `catch` pattern to use `e.userMessage`.

### FE2-021: TypeScript escape hatches — 12 explicit `any`/`as any`/`: any` usages, no `@ts-ignore` count

Severity: **Low**

Evidence (from grep):
- `lib/api.ts:42,90,92,100,105` — request/response bodies typed as `any`.
- `routes/parts/PartCreate.tsx:64`, `parts/detail/PartAddStock.tsx:29`, `PartRemoveStock.tsx:86`, `storage/StorageDetail.tsx:118` — payload-builder objects typed `any`.
- `components/scanner/ScanditScanner.tsx:90,91,97,109` — Scandit SDK's loose typings are read via `(Symbology as any)[key]`, `(session as any).newlyRecognizedBarcode`, etc.
- `components/scanner/ZxingScanner.tsx:71-73,229,239` — WebAudio + getCapabilities typed `any`.
- 0 `@ts-ignore` and 0 `@ts-expect-error` (good).

Impact:

The `any` payload builders mean adding a required server field can ship without a TS error. The Scandit `as any` casts hide the moment its API changes. Net: TypeScript is lying about the safety it advertises.

Fix instruction:

Define a typed `StockAddIn` / `StockMovePayload` / etc. mirroring the backend's `schemas.py`. For Scandit, write a thin `lib/scandit.d.ts` ambient module that types the actual surface used (`newlyRecognizedBarcode`, `Symbology`). Drop `request<T>` accepting `body?: any` in favour of generic `body?: TBody`.

### FE2-022: lazy-loaded routes don't share an error boundary with their parent — Suspense fallback on every route is "Loading…"

Severity: **Low**

Evidence:
- `web/src/App.tsx:131` — top-level `<Suspense fallback={lazyFallback}>` with `<div className="p-6 text-muted">Loading…</div>`.
- 17 lazy imports (Orders, Builds, Reports sub-pages, Projects, Account, WorkspaceSettings) all share that fallback.
- No per-route `<ErrorBoundary>` (only the top-level Sentry one); a chunk-load failure (e.g. CDN cache invalidation while a user is mid-session) shows the Sentry crash card and never recovers without a reload.

Impact:

CSS asset hash rotation between deploys produces "ChunkLoadError" — the user has a stale `index.html` referencing chunks that no longer exist. The Sentry boundary catches it but the only remediation is a manual reload. No retry button.

Fix instruction:

Wrap `<Suspense>` in a `<ChunkLoadErrorBoundary>` that on `ChunkLoadError` does `window.location.reload()` (one-shot, idempotent). Also vary the fallback by route (a Reports skeleton vs an Orders skeleton) to avoid the "blank loading" jank between every nav.

## Coverage gaps

- The frontend test suite is sparse — only `lib/api.test.ts` and `lib/bagCode.test.ts` exist (re-confirms FE-008). I did not run vitest because there is no test that would catch any of the issues above; nothing here was disproven by a missing test result.
- I did not run `tsc --noEmit` (read-only but bypassed by user's "do not run npm" guidance interpreted strictly); given 12 `any` usages the type surface is partially advisory anyway.
- I did not deeply audit `i18n` strategy because the codebase has none — every user-visible string is hard-coded English, including dialog severity labels (`ConfirmDialog.tsx:179-189`). That's a known follow-up rather than a finding.
- I did not audit the public-catalog page (it's behind a token URL not in the SPA's `/api` shape — separate route).
