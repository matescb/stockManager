# Testing

Audience: engineer

How the FE test suite is organised: vitest with a node default + jsdom
opt-in convention, the `crypto.subtle` patch, the `__dom__/` boundary,
and the Playwright smoke spec.

## Tooling

- **vitest** (`^2.1.9`) — unit + DOM tests, run via `npm test`
  (`web/package.json` scripts).
- **@testing-library/react** + **@testing-library/user-event** — DOM
  rendering and synthetic user input.
- **jsdom** — opt-in environment for tests that render React.
- **Playwright** (`^1.48.2`) — single E2E smoke spec, opt-in via
  `npm run test:e2e`.

## Running

```bash
cd web
npm test                # vitest run — all unit + DOM tests
npm run test:watch      # vitest --watch
npm run test:ui         # vitest UI
npm run test:e2e        # playwright (assumes docker compose up at :5173)
```

CI runs `vitest run --coverage`; output lives in `web/coverage/` and is
uploaded as an artifact (TEST-013, see `web/vite.config.ts:54-65`). No
fail-under threshold yet (issue says ratchet later).

## Environment-match-globs convention

`web/vite.config.ts:36-46`:

```ts
test: {
  setupFiles: ["./vitest.setup.ts"],
  environmentMatchGlobs: [
    ["**/__dom__/**", "jsdom"],
    ["**/*.dom.test.{ts,tsx}", "jsdom"],
  ],
  exclude: ["**/node_modules/**", "**/dist/**", "**/e2e/**"],
  …
}
```

Two rules:

1. Most tests are pure helpers and run on the default node env
   (cheaper startup). Examples: `web/src/lib/api.test.ts`,
   `web/src/lib/queryKeys.test.ts`, `web/src/lib/bagCode.test.ts`,
   `web/src/lib/format.test.ts`, `web/src/components/DataTable.test.tsx`.
2. Anything under a `__dom__/` directory or matching `*.dom.test.{ts,tsx}`
   runs against jsdom so RTL can render. The boundary lives in the
   filename / path, not a per-file `// @vitest-environment` comment.

The Playwright `e2e/` directory is excluded explicitly so vitest doesn't
try to load the spec files.

## `vitest.setup.ts` — crypto.subtle patch

`web/vitest.setup.ts`:

```ts
// web/vitest.setup.ts
import { webcrypto } from "node:crypto";
if (!globalThis.crypto || !(globalThis.crypto as any).subtle) {
  // @ts-expect-error — assigning Node's webcrypto to the global slot
  globalThis.crypto = webcrypto;
}
```

vitest 2's default node-environment worker doesn't expose
`globalThis.crypto.subtle` (Node has it under `crypto.webcrypto`). Code
that uses `crypto.subtle.digest` — primarily `bagSignature` in
`web/src/lib/bagCode.ts` — would crash without the patch.

Loaded for **every** test file via `setupFiles`, so DOM tests see the
patch too. The check is conditional, so a real browser env (jsdom doesn't
provide `crypto.subtle` either by default, but if a future env did, we
wouldn't clobber it) keeps its own crypto.

## Pure-helper test pattern

`web/src/components/DataTable.test.tsx` is a model: extract the testable
helpers (`escapeCsvCell`, `buildCsv`, `pruneSelection`) from the
component and test them on the node env. The component-level concerns
(effect prunes selection on rows change, effect resets on `tableId`
change) are exercised either through the pure helpers OR through DOM
tests under `__dom__/`.

The preamble at `DataTable.test.tsx:1-10` documents the pattern
explicitly — the file existed before the repo gained jsdom + RTL, and
the split was an explicit constraint.

## DOM test pattern

```tsx
// web/src/components/__dom__/DataTable.dom.test.tsx:16-37 (excerpt)
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, fireEvent, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DataTable } from "../DataTable";

beforeEach(() => { cleanup(); });

describe("DataTable (DOM)", () => {
  it("renders one row per data item", () => {
    render(<DataTable rows={ROWS} columns={COLUMNS} rowKey={(r) => r.id} />);
    expect(screen.getByText("Banana")).toBeDefined();
    // …
  });
});
```

Conventions:

- `cleanup()` in `beforeEach` — RTL's auto-cleanup isn't wired (no
  `vitest-globals` setup), so each test does it manually.
- `userEvent.setup()` for click/keyboard sequences;
  `fireEvent.keyDown(...)` for low-level synthetic events.
- Assertions against `screen` queries (`getByText`, `getByRole`,
  `queryBy*`).
- Use `within(row)` to scope a query to a specific subtree (see the
  per-row cell-text extraction pattern in
  `DataTable.dom.test.tsx:71-73`).

### Catalogue of DOM tests

| File | Coverage |
|---|---|
| `web/src/components/__dom__/DataTable.dom.test.tsx` | Row render, sort toggle, onRowClick (mouse + Enter + Space), `initialSearch`, multi-select preserves across refetch (FE2-007) |
| `web/src/components/__dom__/DataTable.keyboard.dom.test.tsx` | tabIndex=0 when onRowClick is set, Enter fires, Space fires, no tabIndex when onRowClick is absent |
| `web/src/components/__dom__/ChunkLoadErrorBoundary.dom.test.tsx` | First chunk error → reload + sessionStorage flag; second → retry banner; non-chunk error re-thrown |
| `web/src/components/__dom__/ApiError.render.dom.test.tsx` | Rendering an `ApiError.userMessage` / form-field error from a thrown 422 |
| `web/src/components/__dom__/Form.label-association.dom.test.tsx` | `<label htmlFor>` association on form fields |
| `web/src/lib/__dom__/api.401.dom.test.ts` | `api.get` 401 surfaces `ApiError(401)`; envelope unwrap on 200; `credentials: "include"` on every request |
| `web/src/lib/__dom__/mutations.dom.test.tsx` | `useApiMutation` mutationKey de-dup, isPending gate, ApiError rethrow, 401 → authBus once |
| `web/src/lib/__dom__/useEntityForm.dom.test.tsx` | Form-state hook smoke |
| `web/src/__dom__/App.smoke.dom.test.tsx` | Root `<App>` mounts without throwing |

### Catalogue of pure-helper tests

| File | Coverage |
|---|---|
| `web/src/lib/api.test.ts` | `categoryToUserMessage`, `getConflictDetail`, schema-mismatch `ApiError` shape |
| `web/src/lib/queryKeys.test.ts` | `wsKeyOf` shape, `wsScope`, the invalidation helpers (`archivePartKeys`, `lotMutationKeys`, …) |
| `web/src/lib/bagCode.test.ts` | The three-pass parser, Control Pictures normalisation, signature hash |
| `web/src/lib/format.test.ts` | `formatDate`, `formatDateTime`, `formatMoney` |
| `web/src/components/DataTable.test.tsx` | `escapeCsvCell` (formula injection + RFC 4180), `buildCsv` (BOM + CRLF), `pruneSelection` |
| `web/src/components/__tests__/wsKey-handlers.test.tsx` | `wsKeyOf` outside React doesn't throw; `useWsKey` outside render DOES throw — pins the Rules-of-Hooks contract |
| `web/src/components/__tests__/AttachmentsPanel.test.tsx` | Attachments panel behaviour |

## Mocking `fetch`

The 401 test (`web/src/lib/__dom__/api.401.dom.test.ts:23-31`) shows the
pattern. Spy on `globalThis.fetch` and return a Headers + json() shape:

```ts
// web/src/lib/__dom__/api.401.dom.test.ts:23-31
function mockFetch(status: number, body: unknown, ok = status >= 200 && status < 300) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue({
    ok,
    status,
    statusText: ok ? "ok" : "error",
    headers: new Headers({ "content-type": "application/json" }),
    json: async () => body,
  } as unknown as Response);
}

beforeEach(() => { vi.restoreAllMocks(); });
```

`Headers` matters — `lib/api.ts` checks `content-type` before parsing
JSON.

## Mocking the QueryClient

`web/src/lib/__dom__/mutations.dom.test.tsx:37-46` builds a real
`QueryClient` with the same `MutationCache` + `on401` shape as
`main.tsx`, then wraps in `QueryClientProvider`:

```tsx
// web/src/lib/__dom__/mutations.dom.test.tsx:37-46
function makeClient() {
  return new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
    mutationCache: new MutationCache({ onError: on401 }),
  });
}

function Wrapper({ children, client }: { children: React.ReactNode; client: QueryClient }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
```

Tests that exercise the auth-bus subscribe directly via `authBus.on(...)`
and assert on the listener (line 191-220). `retry: false` is the same
default as production, so a thrown `ApiError(401)` doesn't fire the
listener three times.

## Playwright smoke

`web/playwright.config.ts` + `web/e2e/smoke.spec.ts`. One spec, one
project (`chromium`), `fullyParallel: false`, `workers: 1`. Opt-in via
`npm run test:e2e`; the default `npm test` (vitest) keeps doing what it
does (`playwright.config.ts:9-11`).

The CI job is `playwright-e2e` (GitHub Actions). It brings up the dev
docker-compose stack, polls `/api/health`, then invokes
`npx playwright test --grep @smoke` (`playwright.config.ts:7-13`).

```ts
// web/e2e/smoke.spec.ts:18-57 (excerpt)
test("@smoke signup → create part → add stock → ledger row visible", async ({ page }) => {
  const email = `e2e-${Date.now()}-${…}@x.com`;     // .test TLD is reserved (RFC 6761),
                                                      // pydantic EmailStr 422s on it.
  await page.goto("/signup");
  await page.getByLabel(/email/i).fill(email);
  // …
  await page.waitForURL(/\/parts(\b|$)/, { timeout: 10_000 });

  await page.getByRole("link", { name: /\+ part/i }).first().click();
  await page.getByLabel(/^name$/i).fill("E2E Smoke Resistor");
  await page.getByRole("button", { name: /^create$/i }).click();
  await expect(page.getByText("E2E Smoke Resistor").first()).toBeVisible({ timeout: 10_000 });

  await page.getByRole("link", { name: "Add stock", exact: true }).click();
  await page.getByLabel(/quantity/i).fill("42");
  await page.getByRole("button", { name: /^add$/i }).click();

  await expect(page.getByText("42").first()).toBeVisible({ timeout: 10_000 });
});
```

Two E2E gotchas worth pinning here:

- `.test` is a reserved TLD (RFC 6761) and pydantic's `EmailStr` rejects
  it with 422; use `.com` (`smoke.spec.ts:19-21`).
- `PartsList` renders a `<Link>` (role=link) "+ Part" — match by link
  role, not button (`smoke.spec.ts:35-36`).
- The "Add stock" SubNav tab routes to `/parts/{id}/add`, which is where
  the form lives. The "Stock" tab is read-only
  (`smoke.spec.ts:48-49`).

The suite is intentionally minimal: the v2 plan called out page-level
component tests as out of scope. This is the one end-to-end signal that
the routing + API + ledger glue all line up
(`smoke.spec.ts:11-14`).

`PLAYWRIGHT_BASE_URL` overrides the default `http://localhost:5173`
(`playwright.config.ts:21`).

## Coverage exclusions

`web/vite.config.ts:60-66` — d.ts files, config files, scripts, test
files, and node_modules / dist are excluded. The `__tests__/` path is
excluded too (it carries test files, not production code).

## TODO(verify)

- The repo carries `web/src/lib/__fixtures__/bagSignatures.json` —
  confirm whether it's consumed by `web/src/lib/bagCode.test.ts` or only
  by an offline cross-decoder validation script.
