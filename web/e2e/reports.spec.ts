import type { Locator, Page } from "@playwright/test";

import { test, expect, seedPart } from "./fixtures";

type ReportRoute = {
  path: string;
  tab: string;
  anchor: (page: Page) => Locator;
};

const reportRoutes: ReportRoute[] = [
  {
    path: "/reports/value",
    tab: "Stock value",
    anchor: (page) => page.getByRole("heading", { name: "By currency" }),
  },
  {
    path: "/reports/replenishment-cost",
    tab: "Replenishment cost",
    anchor: (page) => page.getByLabel("Sort"),
  },
  {
    path: "/reports/bom",
    tab: "BOM shortage",
    anchor: (page) => page.getByLabel("Project"),
  },
  {
    path: "/reports/buyability",
    tab: "BOM buyability",
    anchor: (page) => page.getByText("No projects", { exact: true }),
  },
  {
    path: "/reports/expiring",
    tab: "Expiring lots",
    anchor: (page) => page.getByText("All clear", { exact: true }),
  },
  {
    path: "/reports/sourcing-risk",
    tab: "Sourcing risk",
    anchor: (page) => page.getByText("All clear", { exact: true }),
  },
];

for (const report of reportRoutes) {
  test(
    `${report.path} loads + ${report.tab} tab active`,
    { tag: ["@core"] },
    async ({ authedPage }, testInfo) => {
      const { page, request } = authedPage;
      const mpnSuffix = testInfo.testId.replace(/[^a-zA-Z0-9]+/g, "-").slice(0, 48);

      await seedPart(request, {
        name: "E2E Report Part",
        manufacturer: "E2E Fixtures",
        mpn: `E2E-REPORT-${mpnSuffix}`,
      });

      await page.goto(report.path);

      const activeTab = page.getByRole("link", { name: report.tab, exact: true });
      await expect(activeTab).toBeVisible({ timeout: 5_000 });
      await expect(activeTab).toHaveAttribute("aria-current", "page");
      await expect(report.anchor(page)).toBeVisible({ timeout: 5_000 });
      await expect(page.locator("text=Something went wrong")).toHaveCount(0);
    },
  );
}
