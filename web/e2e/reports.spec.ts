import { test, expect, seedPart } from "./fixtures";

type ReportRoute = {
  path: string;
  tab: string;
};

const reportRoutes: ReportRoute[] = [
  {
    path: "/reports/value",
    tab: "Stock value",
  },
  {
    path: "/reports/replenishment-cost",
    tab: "Replenishment cost",
  },
  {
    path: "/reports/bom",
    tab: "BOM shortage",
  },
  {
    path: "/reports/buyability",
    tab: "BOM buyability",
  },
  {
    path: "/reports/expiring",
    tab: "Expiring lots",
  },
  {
    path: "/reports/sourcing-risk",
    tab: "Sourcing risk",
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
      await expect(page.locator('[role="main"], main')).toBeVisible({ timeout: 5_000 });
      await expect(page.locator("text=Something went wrong")).toHaveCount(0);
    },
  );
}
