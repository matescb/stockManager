import { randomUUID } from "node:crypto";
import type { APIRequestContext, Page, TestInfo } from "@playwright/test";

import {
  expect,
  mockSourcingProviders,
  seedBomLine,
  seedPart,
  seedProject,
  test,
} from "./fixtures";

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173";
const OVERRIDE_LINE_ID = "11111111-1111-4111-8111-111111111111";
const OTHER_LINE_ID = "22222222-2222-4222-8222-222222222222";

function uniqueName(prefix: string): string {
  return `${prefix} ${randomUUID().slice(0, 8)}`;
}

function sameOriginHeaders() {
  return {
    origin: BASE_URL,
    referer: `${BASE_URL}/`,
  };
}

async function configureSourcingWorkspace(request: APIRequestContext): Promise<void> {
  const response = await request.patch("/api/workspaces/current", {
    data: {
      sourcing_provider: "trustedparts",
      sourcing_company_id: "e2e-company",
      sourcing_api_key: "e2e-api-key",
      sourcing_country_code: "CZ",
      sourcing_currency_code: "EUR",
      sourcing_preferred_distributors: ["DigiKey", "Mouser"],
      active_countries: ["CZ", "US"],
      active_currencies: ["EUR", "USD"],
      active_distributors: ["DigiKey", "Mouser"],
      sourcing_use_cached_for_dashboards: false,
    },
    headers: sameOriginHeaders(),
  });
  expect(response.ok()).toBe(true);
}

async function seedTwoLineProject(request: APIRequestContext) {
  const project = await seedProject(request, {
    name: uniqueName("E2E Sourcing Project"),
    description: "Projects + BOM sourcing E2E",
  });
  const mcu = await seedPart(request, {
    name: uniqueName("E2E Source MCU"),
    manufacturer: "E2E TestCo",
    mpn: "E2E-PLAN-A",
  });
  const regulator = await seedPart(request, {
    name: uniqueName("E2E Source Regulator"),
    manufacturer: "E2E TestCo",
    mpn: "E2E-PLAN-B",
  });
  await seedBomLine(request, project.id, {
    part_id: mcu.id,
    name: mcu.name,
    quantity: 10,
    designators: ["U1"],
  });
  await seedBomLine(request, project.id, {
    part_id: regulator.id,
    name: regulator.name,
    quantity: 8,
    designators: ["U2"],
  });
  return { project, mcu, regulator };
}

async function sourceBom(page: Page, projectId: string): Promise<void> {
  await page.goto(`/projects/${projectId}/sourcing`);
  await expect(page.getByRole("heading", { name: "Source BOM" })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Source$/ })).toBeEnabled();
  await page.getByRole("button", { name: /^Source$/ }).click();
  await expect(page.getByRole("heading", { name: "Coverage matrix" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "BOM rows" })).toBeVisible();
}

async function openPurchasePlan(page: Page, projectId: string): Promise<void> {
  await sourceBom(page, projectId);
  await expect(page.getByRole("button", { name: "Generate purchase plan" })).toBeEnabled();
  await page.getByRole("button", { name: "Generate purchase plan" }).click();
  const dialog = page.getByRole("dialog", { name: "Generate purchase plan" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: /^Generate$/ }).click();
  await page.waitForURL(new RegExp(`/projects/${projectId}/purchase-plans/[0-9a-f-]+$`));
  await expect(page.getByTestId("purchase-plan-loaded")).toBeVisible();
}

function planLine(page: Page, mpn: string) {
  return page.getByRole("row").filter({ hasText: mpn });
}

async function selectMouserOverride(page: Page): Promise<void> {
  await page.getByTestId(`override-button-${OVERRIDE_LINE_ID}`).click();
  const dialog = page.getByRole("dialog", { name: "Override offer" });
  await expect(dialog).toBeVisible();
  await dialog
    .getByRole("row")
    .filter({ hasText: "Mouser" })
    .getByRole("button", { name: "Select" })
    .click();
  await expect(dialog).toHaveCount(0);
  const row = planLine(page, "E2E-PLAN-A");
  await expect(row).toContainText("Mouser");
  await expect(row).toContainText("10");
  await expect(row).toContainText("1.10 EUR");
}

async function attachScreenshot(testInfo: TestInfo, page: Page, name: string): Promise<void> {
  await testInfo.attach(name, {
    body: await page.screenshot(),
    contentType: "image/png",
  });
}

test.beforeEach(async ({ page }) => {
  await mockSourcingProviders(page, { refreshResponse: "success" });
});

test(
  "create project: form submit routes to /projects/<id>/data with name visible",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page } = authedPage;
    const projectName = uniqueName("E2E Created Project");

    await page.goto("/projects");
    await page.getByRole("link", { name: "+ Project" }).first().click();
    await page.getByLabel("Name *").fill(projectName);
    await page.getByRole("button", { name: /^Create$/ }).click();

    await page.waitForURL(/\/projects\/[0-9a-f-]+\/data$/);
    await expect(page.getByText(projectName).first()).toBeVisible();
    await expect(page.getByLabel("Name")).toHaveValue(projectName);
  },
);

test(
  "BOM line add: seeded part + UI add produces two entries",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, request } = authedPage;
    const project = await seedProject(request, { name: uniqueName("E2E BOM Project") });
    const seededPart = await seedPart(request, {
      name: uniqueName("E2E Seeded BOM Part"),
      mpn: "E2E-BOM-SEEDED",
    });
    const uiPart = await seedPart(request, {
      name: uniqueName("E2E UI BOM Part"),
      mpn: "E2E-BOM-UI",
    });
    await seedBomLine(request, project.id, {
      part_id: seededPart.id,
      name: seededPart.name,
      quantity: 2,
      designators: ["R1", "R2"],
    });

    await page.goto(`/projects/${project.id}/data`);
    await page.getByRole("link", { name: "BOM" }).click();
    await expect(page).toHaveURL(new RegExp(`/projects/${project.id}/bom$`));
    await expect(page.getByText(seededPart.name).first()).toBeVisible();

    await page.getByRole("button", { name: "Add Part" }).click();
    const dialog = page.getByRole("dialog", { name: "Add part from library" });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("Search library").fill(uiPart.name);
    await expect(dialog.getByLabel(`Select ${uiPart.name}`)).toBeVisible();
    await dialog.getByLabel(`Select ${uiPart.name}`).check();
    await dialog.getByRole("button", { name: "Add 1 part to BOM" }).click();

    await expect(page.getByText("Added 1 part to BOM.")).toBeVisible();
    await expect(page.getByText(seededPart.name).first()).toBeVisible();
    await expect(page.getByText(uiPart.name).first()).toBeVisible();
    await expect(page.getByText("2 rows")).toBeVisible();
  },
);

test(
  "sourcing trigger: mocked providers produce coverage card and offer rows",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, request } = authedPage;
    const { project } = await seedTwoLineProject(request);
    await configureSourcingWorkspace(request);

    await page.goto(`/projects/${project.id}/data`);
    await page.getByRole("link", { name: "Source BOM" }).click();
    await expect(page).toHaveURL(new RegExp(`/projects/${project.id}/sourcing$`));
    await expect(page.getByRole("button", { name: /^Source$/ })).toBeEnabled();
    await page.getByRole("button", { name: /^Source$/ }).click();

    await expect(page.getByRole("heading", { name: "Coverage matrix" })).toBeVisible();
    await expect(page.getByText("Lowest total price")).toBeVisible();
    await expect(planLine(page, "E2E-PLAN-A")).toContainText("Mouser");
    await expect(planLine(page, "E2E-PLAN-B")).toContainText("DigiKey");
  },
);

test(
  "override offer: selecting an alternate offer persists in plan state",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, request } = authedPage;
    const { project } = await seedTwoLineProject(request);
    await configureSourcingWorkspace(request);

    await openPurchasePlan(page, project.id);
    await selectMouserOverride(page);

    await expect(page.getByTestId(`override-button-${OVERRIDE_LINE_ID}`)).toHaveClass(/btn-primary/);
  },
);

test(
  "SA-2 invariant: override is preserved across a successful refresh",
  { tag: ["@core"] },
  async ({ authedPage }, testInfo) => {
    const { page, request } = authedPage;
    const { project } = await seedTwoLineProject(request);
    await configureSourcingWorkspace(request);

    await openPurchasePlan(page, project.id);
    await selectMouserOverride(page);
    await attachScreenshot(testInfo, page, "sa-2-before-refresh");

    await page.getByRole("button", { name: "Refresh prices" }).click();
    await expect(page.getByText("Prices refreshed")).toBeVisible();

    const overridden = planLine(page, "E2E-PLAN-A");
    await expect(overridden).toContainText("Mouser");
    await expect(overridden).toContainText("10");
    await expect(overridden).toContainText("1.10 EUR");
    await expect(page.getByTestId(`override-button-${OVERRIDE_LINE_ID}`)).toHaveClass(/btn-primary/);
    await expect(planLine(page, "E2E-PLAN-B")).toContainText("1.80 EUR");
    await attachScreenshot(testInfo, page, "sa-2-after-refresh");
  },
);

test(
  "SA-9 invariant: plan view is restored from TanStack cache on back-navigation",
  { tag: ["@core"] },
  async ({ authedPage }, testInfo) => {
    const { page, request } = authedPage;
    const { project } = await seedTwoLineProject(request);
    await configureSourcingWorkspace(request);

    await openPurchasePlan(page, project.id);
    await expect(page.getByText("Est. cost")).toBeVisible();
    await attachScreenshot(testInfo, page, "sa-9-plan-loaded");

    let purchasePlanGetCount = 0;
    page.on("request", req => {
      if (
        req.method() === "GET" &&
        req.url().includes(`/api/projects/${project.id}/purchase-plans/`)
      ) {
        purchasePlanGetCount += 1;
      }
    });
    await page.evaluate(() => {
      const target = window as Window & {
        __e2eSawPlanLoading?: boolean;
        __e2ePlanLoadingObserver?: MutationObserver;
      };
      target.__e2eSawPlanLoading = document.body.innerText.includes("Loading purchase plan...");
      target.__e2ePlanLoadingObserver = new MutationObserver(() => {
        if (document.body.innerText.includes("Loading purchase plan...")) {
          target.__e2eSawPlanLoading = true;
        }
      });
      target.__e2ePlanLoadingObserver.observe(document.body, {
        childList: true,
        subtree: true,
        characterData: true,
      });
    });

    await page.getByRole("link", { name: "BOM" }).click();
    await expect(page).toHaveURL(new RegExp(`/projects/${project.id}/bom$`));
    await expect(page.getByRole("button", { name: "Add Part" })).toBeVisible();
    await attachScreenshot(testInfo, page, "sa-9-bom-page");

    const unexpectedRequest = page
      .waitForRequest(
        req => req.method() === "GET" &&
          req.url().includes(`/api/projects/${project.id}/purchase-plans/`),
        { timeout: 500 },
      )
      .catch(() => null);
    await page.goBack();
    await expect(page.getByTestId("purchase-plan-loaded")).toBeVisible();
    await expect(page.getByText("Loading purchase plan...")).toHaveCount(0);
    expect(await unexpectedRequest).toBeNull();
    expect(purchasePlanGetCount).toBe(0);
    expect(await page.evaluate(() => {
      const target = window as Window & {
        __e2eSawPlanLoading?: boolean;
        __e2ePlanLoadingObserver?: MutationObserver;
      };
      target.__e2ePlanLoadingObserver?.disconnect();
      return target.__e2eSawPlanLoading;
    })).toBe(false);
    await attachScreenshot(testInfo, page, "sa-9-plan-restored");
  },
);

test(
  "convert plan to orders: confirm flow creates draft orders",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, request } = authedPage;
    const { project } = await seedTwoLineProject(request);
    await configureSourcingWorkspace(request);

    await openPurchasePlan(page, project.id);
    await selectMouserOverride(page);
    await page.getByRole("button", { name: "Create draft orders" }).click();

    await expect(page.getByText("Created 2 draft orders")).toBeVisible();
    await expect(page).toHaveURL(/\/orders$/);
  },
);

test.fixme(
  "real-key sourcing smoke (placeholder)",
  { tag: ["@nightly"] },
  async () => {
    // Follow-up #685: real Mouser/DigiKey/TP key suite.
  },
);
