import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the smoke E2E suite (TEST-004).
 *
 * Local: assumes `docker compose up` is running on
 * http://localhost:5173. Use `npm run test:e2e` to run.
 *
 * CI: TBD — landed alongside the test stack but not yet wired into
 * GitHub Actions (see #116). The suite is opt-in via `npm run test:e2e`
 * so the default `npm test` (vitest) keeps doing what it does.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
