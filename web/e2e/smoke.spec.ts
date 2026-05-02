import { test, expect } from "@playwright/test";

/**
 * Smoke E2E (TEST-004 / @smoke).
 *
 * Walks the canonical happy path: signup → create part → add stock →
 * see the ledger row. Assumes a running docker-compose stack at
 * `PLAYWRIGHT_BASE_URL` (default: http://localhost:5173). Opt in via
 * `npm run test:e2e`.
 *
 * The suite is intentionally minimal — the v2 plan calls out that
 * page-level component tests are out of scope here. This is the one
 * end-to-end signal that the routing + API + ledger glue all line up.
 */

const STRONG_PW = "TestPass-2026-Stronk";

test("@smoke signup → create part → add stock → ledger row visible", async ({ page }) => {
  const email = `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@x.test`;

  // -------------------- signup --------------------
  await page.goto("/signup");
  await page.getByLabel(/email/i).fill(email);
  await page.getByLabel(/^name$/i).fill("e2e");
  await page.getByLabel(/password/i).fill(STRONG_PW);
  await page.getByRole("button", { name: /sign up|create account/i }).click();

  // After signup the app routes into the workspace (parts list or dashboard).
  await page.waitForURL(/\/(parts|dashboard|$)/, { timeout: 10_000 });

  // -------------------- create part --------------------
  await page.goto("/parts");
  await page.getByRole("button", { name: /new part|add part|create/i }).first().click();
  await page.getByLabel(/^name$/i).fill("E2E Smoke Resistor");
  await page.getByRole("button", { name: /save|create/i }).click();

  // The part page should render the part name.
  await expect(page.getByText("E2E Smoke Resistor")).toBeVisible({ timeout: 10_000 });

  // -------------------- add stock --------------------
  // Navigate to the part's stock tab; the exact selector depends on the
  // app routing. This walks the visible UI rather than poking URLs.
  await page.getByRole("link", { name: /stock/i }).first().click();
  await page.getByRole("button", { name: /add stock|receive|add/i }).first().click();
  await page.getByLabel(/quantity/i).fill("42");
  await page.getByRole("button", { name: /save|add|confirm/i }).click();

  // -------------------- ledger row visible --------------------
  await expect(page.getByText(/42/)).toBeVisible({ timeout: 10_000 });
});
