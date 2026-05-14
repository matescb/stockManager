import { test, expect } from "./fixtures";

test(
  "settings nav: /settings/account and /settings/workspace both load",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page } = authedPage;

    await page.goto("/settings/account");
    await expect(page.getByRole("heading", { name: "Account", level: 1 })).toBeVisible({
      timeout: 5_000,
    });

    await page.goto("/settings/workspace");
    await expect(page.getByRole("heading", { name: "Workspace", level: 1 })).toBeVisible({
      timeout: 5_000,
    });
  },
);

test(
  "workspace distributor active-list: >=1 option present, selection round-trips",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page } = authedPage;

    await page.goto("/settings/workspace");

    const activeLists = page.locator("form", {
      has: page.getByRole("heading", {
        name: "Active currencies / countries / distributors",
        level: 2,
      }),
    });
    await expect(activeLists).toBeVisible({ timeout: 5_000 });
    await activeLists.scrollIntoViewIfNeeded();

    const distributorGroup = activeLists.getByRole("group", { name: "Active distributors" });
    const distributorOptions = distributorGroup.getByRole("checkbox");
    await expect.poll(async () => distributorOptions.count()).toBeGreaterThan(0);

    const arrow = distributorGroup.getByLabel("Arrow", { exact: true });
    await expect(arrow).toBeVisible({ timeout: 5_000 });
    await expect(arrow).not.toBeChecked();

    await arrow.check();
    await activeLists.getByRole("button", { name: "Save active lists" }).click();
    await expect(page.getByText("Active lists saved.")).toBeVisible({ timeout: 10_000 });

    await page.reload();

    const reloadedActiveLists = page.locator("form", {
      has: page.getByRole("heading", {
        name: "Active currencies / countries / distributors",
        level: 2,
      }),
    });
    const reloadedArrow = reloadedActiveLists
      .getByRole("group", { name: "Active distributors" })
      .getByLabel("Arrow", { exact: true });

    await expect(reloadedArrow).toBeVisible({ timeout: 5_000 });
    await expect(reloadedArrow).toBeChecked();
  },
);

test(
  "workspace scanner choice: ZXing option is always present",
  { tag: ["@core"] },
  async ({ authedPage }) => {
    const { page } = authedPage;

    await page.goto("/settings/workspace");

    const scannerCard = page.locator(".card", {
      has: page.getByRole("heading", { name: "Scanner", level: 2 }),
    });
    await expect(scannerCard).toBeVisible({ timeout: 5_000 });
    await scannerCard.scrollIntoViewIfNeeded();

    const scannerSelect = scannerCard.getByRole("combobox");
    await expect(scannerSelect.locator("option").filter({ hasText: /Open-source.*ZXing/ })).toHaveCount(1);
  },
);
