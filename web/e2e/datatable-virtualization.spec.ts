import { expect, test } from "@playwright/test";

test.describe("DataTable virtualization", () => {
  test("bounds mounted rows for a 10k-row table @core", async ({ page }) => {
    await page.goto("/e2e/datatable-virtualization.html");

    await expect(page.getByText("Part 00000")).toBeVisible();
    await expect
      .poll(() => page.locator("tbody tr").count())
      .toBeLessThan(200);

    await page.getByPlaceholder("Search…").fill("Part 09999");
    await expect(page.getByText("Part 09999")).toBeVisible();
    await expect(page.locator("tbody tr")).toHaveCount(1);
  });
});
