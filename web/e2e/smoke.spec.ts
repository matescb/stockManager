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
  // `.test` is a reserved special-use TLD (RFC 6761) and pydantic's
  // EmailStr (via email-validator) rejects it with 422; use `.com`.
  const email = `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@x.com`;

  // -------------------- signup --------------------
  await page.goto("/signup");
  await page.getByLabel(/email/i).fill(email);
  await page.getByLabel(/^name$/i).fill("e2e");
  await page.getByLabel(/password/i).fill(STRONG_PW);
  await page.getByRole("button", { name: /create account/i }).click();

  // After signup the app routes into the workspace at /parts.
  await page.waitForURL(/\/parts(\b|$)/, { timeout: 10_000 });

  // -------------------- create part --------------------
  // PartsList renders a `<Link>` (role=link) "+ Part" → /parts/create,
  // not a button — match by link role.
  await page.getByRole("link", { name: /\+ part/i }).first().click();
  await page.getByLabel(/^name$/i).fill("E2E Smoke Resistor");
  await page.getByRole("button", { name: /^create$/i }).click();

  // After create the app routes to /parts/{id}/info, which renders the
  // part name in the EntityHeader.
  await expect(page.getByText("E2E Smoke Resistor").first()).toBeVisible({
    timeout: 10_000,
  });

  // -------------------- add stock --------------------
  // The "Add stock" SubNav tab (role=link) routes to /parts/{id}/add,
  // which is where the form lives — the "Stock" tab is read-only.
  await page.getByRole("link", { name: "Add stock", exact: true }).click();
  await page.getByLabel(/quantity/i).fill("42");
  await page.getByRole("button", { name: /^add$/i }).click();

  // -------------------- ledger row visible --------------------
  // The success path navigates to /parts/{id}/stock; the on-hand stat
  // and the ledger row both render the new quantity.
  await expect(page.getByText("42").first()).toBeVisible({ timeout: 10_000 });
});
