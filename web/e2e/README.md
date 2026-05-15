# Playwright E2E Tests

Audience: engineer

Authoring guide for the Playwright suite under `web/e2e/`.

## Writing a new test

Import the fixture surface through the local barrel:

```ts
import { test, expect, seedPart } from "./fixtures";

test("part details render seeded stock", { tag: ["@core"] }, async ({ authedPage }) => {
  const { page, request } = authedPage;
  const part = await seedPart(request, { name: "E2E Part" });
  await page.goto(`/parts/${part.id}/info`);
  await expect(page.getByText("E2E Part")).toBeVisible();
});
```

Use structured tags (`{ tag: ["@core"] }`). Inline title tags still grep, but new specs should not use them.

## Tag Taxonomy

| Tag | Runs | Use when |
|---|---|---|
| `@smoke` | `playwright-e2e`, deploy-gating | One minimal full-stack path must stay under the deploy budget. Adding a smoke test requires reviewer sign-off. |
| `@core` | `playwright-core`, advisory and label-gated | Domain-critical flows that should run on relevant frontend/testing PRs but are not deploy-gating yet. |
| `@nightly` | `playwright-nightly`, scheduled/manual | Heavy provider-mocked or multi-step flows that are useful signal but too slow or broad for PRs. |

Chromium is the only browser target.

## Seed And Mock Helpers

- `authedPage` signs up a real user through `/api/auth/signup` and returns `{ page, request, email, workspaceId, userId }`.
- `authedRequest` is `page.request`; it shares the signed-in page cookie jar. Do not use Playwright's top-level `request` fixture for authenticated setup.
- `seedPart`, `seedStorage`, `seedStock`, and `seedScanImport` call public `/api/*` endpoints with the real session cookie. There are no backend test-mode endpoints or database shortcuts.
- `seedProject` and `seedBomLine` are typed stubs for the follow-up E2E issues.
- Provider helpers use `page.route()` and return backend-shaped envelopes (`{ data, status }`), matching `web/src/lib/api.ts`.
- Scan-import bag flows should use `/parts/scan-import` → `Manual entry` → `Bag code` → `Add bag`; keep those tests on the paste path instead of camera-decoder shims.

## Page Objects

Use `pages/PartDetailPage.ts` for part-detail tab navigation, stock mutations, and shared locators. POMs expose helpers and locators only; assertions stay in specs.

## Selectors

Prefer user-facing locators:

1. `getByRole`
2. `getByLabel`
3. `getByText` for stable visible copy
4. `data-testid` only when the UI has no accessible contract

Every new `data-testid` needs a PR-body justification.

## Waits

`waitForTimeout` is banned in `*.spec.ts` and CI greps for it. Use web-first assertions, `expect.poll`, URL waits, response waits, or route handlers.

Escape hatch: `stock.spec.ts` has the reviewed #688 ledger-ordering exception, marked inline with `FIXME-allowed`; do not add another without reviewer sign-off.

## Debugging

```bash
cd web
npx playwright test --project=smoke --headed --debug
npx playwright test --project=core --pass-with-no-tests --trace on
npx playwright show-report
```

Run against a local stack started with `make dev-up` from the repo root.

## Quarantine

None.

TODO(E2E): nightly failure-opens-issue automation belongs in a follow-up.
