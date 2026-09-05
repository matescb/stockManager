import { randomUUID } from "node:crypto";
import type { APIRequestContext } from "@playwright/test";

import {
  expect,
  mockSourcingProviders,
  seedBomLine,
  seedPart,
  seedProject,
  test,
} from "./fixtures";

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173";

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

test(
  "reusable modal stays scrollable in a mobile viewport",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page, request } = authedPage;
    await page.setViewportSize({ width: 375, height: 667 });
    await mockSourcingProviders(page);
    await configureSourcingWorkspace(request);

    const project = await seedProject(request, {
      name: uniqueName("E2E Mobile Modal Project"),
      description: "Mobile modal smoke",
    });
    const part = await seedPart(request, {
      name: uniqueName("E2E Mobile Modal Part"),
      manufacturer: "E2E TestCo",
      mpn: "E2E-MOBILE-MODAL",
    });
    await seedBomLine(request, project.id, {
      part_id: part.id,
      name: part.name,
      quantity: 10,
      designators: ["U1"],
    });

    await page.goto(`/projects/${project.id}/sourcing`);
    // "Sourcing" is a tab in the shared ProjectLayout strip now; the page
    // no longer redraws a breadcrumb and an <h1> over that header.
    await expect(page.getByRole("button", { name: /^Source$/ })).toBeEnabled();
    await page.getByRole("button", { name: /^Source$/ }).click();
    await expect(page.getByRole("heading", { name: "Coverage matrix" })).toBeVisible();

    await page.getByRole("button", { name: "Generate purchase plan" }).click();
    const dialog = page.getByRole("dialog", { name: "Generate purchase plan" });
    await expect(dialog).toBeVisible();

    const panel = dialog.locator(":scope > div").first();
    const metrics = await panel.evaluate((element) => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return {
        height: rect.height,
        maxHeight: Number.parseFloat(style.maxHeight),
        overflowY: style.overflowY,
        viewportHeight: window.innerHeight,
      };
    });

    expect(metrics.overflowY).toBe("auto");
    expect(metrics.maxHeight).toBeCloseTo(metrics.viewportHeight * 0.9, 0);
    expect(metrics.height).toBeLessThanOrEqual(metrics.viewportHeight * 0.9 + 1);
    await expect(dialog.getByRole("button", { name: "Cancel" })).toBeVisible();
    await expect(dialog.getByRole("button", { name: /^Generate$/ })).toBeVisible();
  },
);
