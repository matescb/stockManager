import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the E2E suite.
 *
 * Local: assumes `docker compose up` is running on
 * http://localhost:5173. Use `npm run e2e:smoke` to run the deploy-gating
 * smoke walk.
 *
 * CI: runs `@smoke` in the `playwright-e2e` job against dev compose.
 * The separate prod compose validation sets PLAYWRIGHT_PROJECT_SET=prod-smoke
 * so its legacy unauthenticated check does not join the smoke/core/nightly
 * taxonomy.
 */
const desktopChrome = { ...devices["Desktop Chrome"] };
const e2eProjects = [
  { name: "smoke", grep: /@smoke/, use: desktopChrome },
  { name: "core", grep: /@core/, grepInvert: /@nightly/, use: desktopChrome },
  { name: "nightly", grep: /@nightly/, use: desktopChrome },
];

const prodSmokeProjects = [
  { name: "prod-smoke", grep: /@prod-smoke/, use: desktopChrome },
];

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173",
    trace: "on-first-retry",
  },
  projects: process.env.PLAYWRIGHT_PROJECT_SET === "prod-smoke"
    ? prodSmokeProjects
    : e2eProjects,
});
